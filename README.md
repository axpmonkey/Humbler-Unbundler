## Preface

This tool doesn't really leverage anything too weird and is very simple in execution (it's all front end api calls that the websites make)

To my knowledge there are no inherent dangers of using this tool, I personally redeemed over 500 games (which took 12 or so hours)

HOWEVER,

```
Please use this tool at your own risk
I make no guarantees involving the of the safety of using this tool.
```

## Prerequisites

- Firefox (You must change the script and potentially install additional drivers to use Chrome, explained below)
- [uv](https://docs.astral.sh/uv/) (recommended) — handles Python and dependencies for you

The script declares its own dependencies inline (PEP 723), so with uv you don't need to install anything manually. Just run it with `uv run` (see Usage).

<details>
<summary>Not using uv?</summary>

- Python 3
- pip (usually comes with python)
- selenium (`pip install selenium`)
</details>

## Usage

This script is by default set up to use Firefox change the first line if you would like to try using a different browser
I have tested the script with Firefox and Chrome

`from selenium.webdriver import Firefox as Browser --> from selenium.webdriver import Chrome as Browser`

https://www.selenium.dev/documentation/webdriver/browsers/
https://chromedriver.chromium.org/getting-started

It should work on whatever you need, let me know if it does!

### Purpose

This is a tool for automatically redeeming humble bundle steam keys to steam. With the script as-is you can expect about 41 games per hour redeemed automatically

`uv run unbundlerize.py` (recommended) or `python unbundlerize.py` or `py unbundlerize.py`

## Flow

1. Web browser will open
   - Address bar will be red with a little robot symbol at the start
   - Browser should not grab credentials already used in the browser of choice
   - Browser extentions should be disabled
1. Hublebundle login page will open
   - Log In
   - Press enter in `terminal`
1. Pages will flash rapidly grabbing json info for processing
1. Steam store login page will open (`store.steampowered.com/login`)
   - Log In
   - Press enter in `terminal`
1. Console will output messages about redemption, the process may take quite a while (it did for me)
   - Open the browser console and go to the network tab (usually F12) if you want to see the requests being made
1. Browser will close when complete
1. File .used_keys will be generated for future runs of the tool (skips redemption call so we don't waste time on stuff we know is already used)
   - It is written atomically (so an interrupted run can't corrupt it), and a `.used_keys.bak` copy is kept on start and before the final save
   - If the file is ever found corrupt it is moved to `.used_keys.corrupt` and the run starts fresh

## Settings

defaults are `retry_rate_seconds = 60 | redeem_cooldown_minutes = 10 | loglevel = INFO`

```
uv run unbundlerize.py -r <retry_rate_seconds> -c <redeem_cooldown_minutes> --log {DEBUG,INFO,WARNING,ERROR,CRITICAL}
```

Run `uv run unbundlerize.py -h` for the full help. `-r`/`-c` must be integers and `--log` must be one of the listed levels, otherwise the script exits with an error.

If Steam keeps rate-limiting, the script stops after 6 consecutive cooldowns (rather than looping forever) and saves progress so you can resume later.

**DO NOT POST DEBUG LEVEL LOGS TO THE INTERNET, THEY MAY CONTAIN UNUSED KEYS**

```
With settings 30 sec, 30 min it took me quite a while because it wouldn't have stopped being angry after 30 minutes and then it would take an hour
with settings 30 sec, 10 min I had way more success and it would take 20 - 50 minutes between request sessions, idk why tbh

Please report back how/what other stuff worked for you if you have the time!
```
