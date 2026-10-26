# /// script
# requires-python = ">=3.9"
# dependencies = ["selenium"]
# ///

from selenium.webdriver import Firefox as Browser
from json import loads, dumps, JSONDecodeError
from os import chmod, environ, path, replace, umask
from shutil import copyfile
from time import sleep
from datetime import datetime
from urllib.parse import quote
from sys import argv, stdout
import argparse, logging

# Please report back what worked for you (how many games in a row/how long it took) if you have the time!
# Make an issue! https://github.com/gr8engineer2b/Humbler-Unbundler/issues

USED_KEYS_FILE = "./.used_keys"
BACKUP_FILE = USED_KEYS_FILE + ".bak"
# Stop after this many back-to-back Steam rate-limit cooldowns instead of looping forever
MAX_CONSECUTIVE_COOLDOWNS = 6
# Steam purchase_result_details codes that will never succeed on a retry. They are
# recorded in .used_keys so later runs don't spend rate-limited attempts on them.
# Codes are from SteamKit's EPurchaseResultDetail enum.
PERMANENT_FAILURES = {
  9: "already redeemed to this account",
  13: "not available in your region",
  14: "invalid key",
  15: "owned by a different account",
  24: "requires a base game you don't own",
  50: "cannot be redeemed through Steam key activation",
}
RATE_LIMITED = 53
# Fields that hold secrets in Humble API data: the Steam key itself, and the
# order key, which links to the order's download page
SENSITIVE_FIELDS = ("redeemed_key_val", "gamekey")


def parse_args(args):
  parser = argparse.ArgumentParser(
      description="Bulk-redeem Humble Bundle Steam keys into your Steam account.")
  parser.add_argument("-r", dest="retry_rate_seconds", type=int, default=90,
                      help="seconds to wait between redeem attempts (default: 90, ~40/hour to stay under Steam's ~50/hour activation cap)")
  parser.add_argument("-c", dest="redeem_cooldown_minutes", type=int, default=10,
                      help="minutes to wait when Steam rate-limits us (default: 10)")
  parser.add_argument("--log", dest="loglevel", default="INFO", type=str.upper,
                      choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
                      help="logging level (default: INFO)")
  return parser.parse_args(args)


def is_ok(response):
  # Steam/Humble return success as either a bool or a string depending on endpoint
  return response.get("success") in (True, "true")


def parse_response(raw):
  # The remote APIs return JSON; if we get HTML instead it usually means the
  # session expired. Abort loudly so the caller's finally-block preserves progress.
  try:
    return loads(raw)
  except (JSONDecodeError, TypeError):
    snippet = raw[:200] if isinstance(raw, str) else raw
    logging.error("Expected JSON but got something else (login expired?): %r", snippet)
    raise RuntimeError("Non-JSON response from remote API; aborting to preserve progress")


def redact(data):
  # Copy of API data that is safe to log: secret values are cut to their last
  # 4 characters, enough to tell entries apart while debugging
  if isinstance(data, dict):
    return {k: f"...{v[-4:]}" if k in SENSITIVE_FIELDS and isinstance(v, str) else redact(v)
            for k, v in data.items()}
  if isinstance(data, list):
    return [redact(v) for v in data]
  return data


def restrict_permissions(filename):
  # The used keys file and its copies hold plain-text keys; keep them owner-only
  if path.exists(filename):
    chmod(filename, 0o600)


def fetch_json(driver, url):
  # Request the API from inside the logged-in page so the session cookies go
  # along, and read the raw body. Navigating to view-source: is blocked by
  # Firefox's WebDriver, and the plain URL renders in its JSON viewer.
  # The URL is passed as an argument rather than baked into the source string.
  js = '''var xhr = new XMLHttpRequest();
  xhr.open('GET', arguments[0], false);
  xhr.send();
  return xhr.response;'''
  return parse_response(driver.execute_script(js, url))


def backup_used_keys():
  if path.exists(USED_KEYS_FILE):
    copyfile(USED_KEYS_FILE, BACKUP_FILE)
    # copyfile keeps an existing destination's old mode, so tighten it explicitly
    restrict_permissions(BACKUP_FILE)
    logging.info("Backed up used keys file to %s", BACKUP_FILE)


def save_used_keys(used_keys):
  # Atomic write: write to a temp file then replace, so an interrupted write can
  # never leave the real file half-written / corrupt.
  tmp = USED_KEYS_FILE + ".tmp"
  with open(tmp, "w", encoding="utf8") as f:
    f.write(dumps(used_keys))
  replace(tmp, USED_KEYS_FILE)


def load_used_keys():
  if not path.exists(USED_KEYS_FILE):
    logging.info("Used keys file does not exist, creating")
    save_used_keys({})
    logging.info("Successfully created used keys file")
    return {}
  logging.info("Used keys file exists")
  restrict_permissions(USED_KEYS_FILE)
  with open(USED_KEYS_FILE, "r", encoding="utf8") as f:
    text = f.read()
  if not text.strip():
    return {}
  try:
    return loads(text)
  except JSONDecodeError:
    quarantine = USED_KEYS_FILE + ".corrupt"
    logging.error("Used keys file is corrupt; quarantining to %s and starting fresh", quarantine)
    copyfile(USED_KEYS_FILE, quarantine)
    restrict_permissions(quarantine)
    return {}


def collect_keys(driver):
  logging.info("Opening login page for humble bundle")
  driver.get("https://www.humblebundle.com/login")
  logging.info("Waiting for user input")
  input("Once logged in, Press Enter:")

  logging.info("Received input, getting owned bundles json")
  orders = fetch_json(driver, "https://www.humblebundle.com/api/v1/user/order")
  logging.debug(redact(orders))

  try_redeem = []
  needs_reveal = []
  logging.info("Getting individual keys from bundles")
  for gamekey in orders:
    gk = gamekey['gamekey']
    items = fetch_order_keys(driver, gk)
    if items is None:
      continue
    logging.debug(redact(items))
    logging.info("Sorting keys")
    for item in items:
      logging.debug(redact(item))
      if item.get("key_type") != "steam":
        logging.info("Key is not a steam key")
        continue
      if item.get("redeemed_key_val"):
        logging.info("Key already revealed")
        try_redeem.append(item)
      else:
        logging.info("Key not yet revealed")
        needs_reveal.append(item)

  # humble bundle has a weird system where you have to "reveal" keys, and in order
  # to get the keys from the api calls they need to be revealed first
  for item in needs_reveal:
    # Values are passed as execute_script arguments and URL-encoded in JS rather
    # than baked into the source string, so a stray quote in API data can't break
    # out of the literal and inject script into the authenticated session.
    js = '''var xhr = new XMLHttpRequest();
    xhr.open('POST', 'https://www.humblebundle.com/humbler/redeemkey', false);
    xhr.setRequestHeader('Content-type', 'application/x-www-form-urlencoded');
    xhr.send('key=' + encodeURIComponent(arguments[0]) +
             '&keyindex=' + encodeURIComponent(arguments[1]) +
             '&keytype=' + encodeURIComponent(arguments[2]));
    return xhr.response;'''
    logging.info("Attempting to reveal key")
    logging.debug(redact(item))
    response = parse_response(driver.execute_script(
        js, item['gamekey'], item['keyindex'], item['machine_name']))
    if is_ok(response):
      logging.info("Key revealed")
      all_tpks = fetch_order_keys(driver, item['gamekey'])
      if all_tpks is None:
        continue
      # match on the keyindex field rather than trusting array order
      revealed = next((t for t in all_tpks if t.get("keyindex") == item["keyindex"]), None)
      if revealed is None:
        logging.warning("Could not locate revealed key by keyindex, skipping")
        continue
      logging.debug(redact(revealed))
      try_redeem.append(revealed)

  return try_redeem


def fetch_order_keys(driver, gk):
  # Returns the order's key list, or None (after logging) if the response isn't
  # shaped as expected, so one odd order doesn't abort the whole run
  contents = fetch_json(driver, f"https://www.humblebundle.com/api/v1/orders?all_tpkds=true&gamekeys={quote(gk, safe='')}")
  try:
    return contents[gk]["tpkd_dict"]["all_tpks"]
  except (KeyError, TypeError):
    logging.warning("Unexpected order data for order ...%s, skipping it", gk[-4:])
    return None


def redeem_keys(driver, try_redeem, used_keys, retry_rate_seconds, redeem_cooldown_minutes):
  logging.info("Opening login page for Steam")
  driver.get("https://store.steampowered.com/login/")
  logging.info("Waiting for user input")
  input("Once logged in, Press Enter:")

  logging.info("Received input, getting session id for redemption")
  driver.get("https://store.steampowered.com/account/registerkey")
  sessionid = driver.execute_script("return g_sessionID")
  if not sessionid:
    raise RuntimeError("Could not read Steam session id (g_sessionID). Are you logged in?")

  # To handle recoverable circumstances we pop(0) entries off the top of the list.
  # A for loop would skip items in a case like "too many requests from this ip".
  logging.info("Entering redeem loop")
  consecutive_cooldowns = 0
  while try_redeem:
    item = try_redeem[0]
    logging.debug(redact(item))
    logging.info("Checking for key in .used_keys")
    key_val = item.get('redeemed_key_val')
    human_name = item.get('human_name', '<unknown>')
    if not key_val:
      try_redeem.pop(0)
      logging.warning("Item has no redeemed key value, skipping: %s", human_name)
      continue
    # we do not want to repeat redemption attempts because of steam limits
    if used_keys.get(key_val):
      try_redeem.pop(0)
      logging.info("Key existed, skipped")
      continue

    js = '''var xhr = new XMLHttpRequest();
    xhr.open('POST', 'https://store.steampowered.com/account/ajaxregisterkey/', false);
    xhr.setRequestHeader('Content-type', 'application/x-www-form-urlencoded');
    xhr.send('product_key=' + encodeURIComponent(arguments[0]) +
             '&sessionid=' + encodeURIComponent(arguments[1]));
    return xhr.response;'''
    logging.info("Attempting to redeem key")
    response = parse_response(driver.execute_script(js, key_val, sessionid))
    logging.debug(response)

    # success is True/"true"; otherwise inspect purchase_result_details.
    # Every case except a rate limit pops the key off the top of the list.
    detail = response.get("purchase_result_details")
    if is_ok(response):
      consecutive_cooldowns = 0
      used_keys[key_val] = "successfully redeemed"
      try_redeem.pop(0)
      logging.info(f"Successfully redeemed {human_name}")
    elif detail in PERMANENT_FAILURES:
      consecutive_cooldowns = 0
      used_keys[key_val] = PERMANENT_FAILURES[detail]
      try_redeem.pop(0)
      logging.info(f"{human_name}: {PERMANENT_FAILURES[detail]}")
    elif detail == RATE_LIMITED:
      consecutive_cooldowns += 1
      if consecutive_cooldowns > MAX_CONSECUTIVE_COOLDOWNS:
        raise RuntimeError(
            f"Steam still rate-limiting after {MAX_CONSECUTIVE_COOLDOWNS} cooldowns; "
            "stopping to preserve progress")
      logging.info(f"Steam is disallowing redeem due to too many requests, waiting for a while ({redeem_cooldown_minutes} min) and will continue...")
      # occasionally write to file so as not to lose progress
      logging.info("Writing keys to file")
      save_used_keys(used_keys)
      logging.info("Finished writing keys to file")
      sleep(redeem_cooldown_minutes * 60)  # steam got angry, sleep for a number of minutes
      continue
    else:
      # Not recorded, so the key is retried on the next run
      consecutive_cooldowns = 0
      try_redeem.pop(0)
      logging.warning(f"The following response was not handled {response}")

    # pace attempts to stay under Steam's rate limit; no need after the last key
    if try_redeem:
      sleep(retry_rate_seconds)


def main():
  args = parse_args(argv[1:])
  # Files this run creates (logs, used keys, backups) are readable by the owner only
  umask(0o077)

  logname = datetime.now().strftime("unbundler_%Y%m%d_%H%M%S.log")
  logging.basicConfig(filename=logname,
                      filemode='a',
                      format='%(asctime)s,%(msecs)d %(name)s %(levelname)s %(message)s',
                      datefmt='%H:%M:%S',
                      level=getattr(logging, args.loglevel))
  logging.getLogger().addHandler(logging.StreamHandler(stdout))

  logging.info("BEGIN!")

  logging.info("Checking for used keys file")
  used_keys = load_used_keys()
  # back up the last-known-good file before we touch anything
  backup_used_keys()

  logging.info("Initializing Browser")
  # Stop Selenium Manager (which locates geckodriver) from sending usage stats to plausible.io
  environ["SE_AVOID_STATS"] = "true"
  driver = Browser()
  # Ensure progress is always saved and the browser always closes, even on error
  try:
    try_redeem = collect_keys(driver)
    redeem_keys(driver, try_redeem, used_keys, args.retry_rate_seconds, args.redeem_cooldown_minutes)
  finally:
    logging.info("Backing up before final save")
    backup_used_keys()
    logging.info("Writing keys to file")
    save_used_keys(used_keys)
    driver.quit()
  logging.info("ALL DONE!")


if __name__ == "__main__":
  main()
