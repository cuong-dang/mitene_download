"""Download medias from https://mitene.us/ or https://family-album.com/"""

__version__ = "0.2.1"

import argparse
import asyncio
import datetime
import glob
import json
import mimetypes
import os
import platform
import sys
import urllib.parse
from typing import Awaitable

import aiohttp


async def gather_with_concurrency(n: int, *tasks: Awaitable[None]) -> None:
  """Like asyncio.gather but limit the number of concurent tasks."""
  semaphore = asyncio.Semaphore(n)

  async def sem_task(task: Awaitable[None]) -> None:
    async with semaphore:
      await task

  await asyncio.gather(*(sem_task(task) for task in tasks))


async def download_media(
  session: aiohttp.ClientSession,
  url: str,
  destination_filename: str,
  media: dict,
  verbose: bool,
) -> None:
  """Download one media from URL"""
  if not os.path.exists(destination_filename):
    if verbose:
      print(f"Downloading {media['uuid']} ⏳", flush=True)
    with open(destination_filename + ".tmp", "wb") as f:
      r = await session.get(url)
      r.raise_for_status()
      async for chunk in r.content.iter_chunked(1024):
        f.write(chunk)
    os.rename(destination_filename + ".tmp", destination_filename)
    if media["comments"]:
        await save_comments(destination_filename, media["comments"])
  elif verbose:
    print(f"{media['uuid']} already downloaded ✔️", flush=True)


async def save_comments(destination_filename: str, comments: list) -> None:
  comment_filename = os.path.splitext(destination_filename)[0] + ".md"
  with open(comment_filename + ".tmp", "w", encoding="utf-8") as comment_f:
    for comment in comments:
      if not comment["isDeleted"]:
        comment_f.write(
          f'**{comment["user"]["nickname"]}**: {comment["body"]}\n\n'
        )
  os.rename(comment_filename + ".tmp", comment_filename)


async def async_main() -> None:
  parser = argparse.ArgumentParser(prog="mitene_download", description=__doc__)
  parser.add_argument(
    "album_url",
    help="""
  URL of the album.

  This is the URL obtained by inviting a family member for the web version.
  """,
  )

  parser.add_argument("--destination-directory", default="out")
  parser.add_argument("-p", "--password")
  parser.add_argument("-v", "--verbose", action="store_true")

  args = parser.parse_args()

  os.makedirs(args.destination_directory, exist_ok=True)
  # cleanup temp files from previous run, if interrupted
  for tmp_file in glob.glob(os.path.join(args.destination_directory, "*.tmp")):
    os.unlink(tmp_file)

  download_coroutines = []
  async with aiohttp.ClientSession(
    timeout=aiohttp.ClientTimeout(total=datetime.timedelta(minutes=30).total_seconds())
  ) as session:
    page = 1
    while True:
      r = await session.get(f"{args.album_url}?page={page}")
      response_text = await r.text()
      if page == 1 and "Please enter your password" in response_text:
        if not args.password:
          print(
            "Album is password protected, please specify password with --password",
            file=sys.stderr,
          )
          sys.exit(1)
        authenticity_token = response_text.split('name="authenticity_token" value="')[
          1
        ].split('"')[0]
        assert authenticity_token, "Could not parse authenticity token"
        r = await session.post(
          f"{args.album_url}/login",
          data={
            "session[password]": args.password,
            "authenticity_token": authenticity_token,
          },
        )
        if r.url.path.endswith("/login"):
          print("Could not authenticate, maybe password is incorrect", file=sys.stderr)
          sys.exit(1)
        continue

      page_text = response_text.split("//<![CDATA[\nwindow.gon={};gon.media=")[1].split(
        ";gon.familyUserIdToColorMap="
      )[0]
      data = json.loads(page_text)

      page += 1
      if not data["mediaFiles"]:
        break
      for media in data["mediaFiles"]:
        filename = urllib.parse.urlparse(
          media.get("expiringVideoUrl", media["expiringUrl"])
        ).path.split("/")[-1]
        filename = f'{media["tookAt"]}-{filename}'
        if platform.system() == "Windows":
          filename = filename.replace(":", "")
        if not os.path.splitext(filename)[1]:
          filename = filename + mimetypes.guess_extension(media["contentType"])
        destination_filename = os.path.join(
          args.destination_directory,
          filename,
        )

        download_coroutines.append(
          download_media(
            session,
            f"{args.album_url}/media_files/{media['uuid']}/download",
            destination_filename,
            media,
            args.verbose,
          )
        )

    await gather_with_concurrency(4, *download_coroutines)
  await session.close()


def main() -> None:
  loop = asyncio.get_event_loop()
  loop.run_until_complete(async_main())


if __name__ == "__main__":
  main()
