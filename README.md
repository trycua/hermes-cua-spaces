# Hermes on Cua Spaces

Run Hermes Agent's terminal, file and code tools inside a [Cua Space](https://cua.ai/spaces): an isolated machine you can watch live, on your own hardware or in your own cloud account.

Hermes still runs where it runs today. When it needs to run a command, write a file or execute code, that work happens in the Space instead of on your computer. Because the Space is the sandbox, Hermes skips its risky-command prompts for Spaces this plugin creates.

Hermes users: get your first month of Cua Spaces Pro or Teams free with code `HERMES-3EPX2A` (first 50, until 15 Nov).

```
you  ->  Hermes (your machine)  ->  this plugin  ->  Cua Space (an isolated Linux, macOS or Windows machine)
                                                      terminal, files, code run here
```

## Install in 3 steps

1. Install Cua and sign in (sign-in is only needed for Spaces on your cua.ai account; a local Space needs none):

   ```bash
   curl -fsSL https://cua.ai/install.sh | sh
   cua auth login
   ```

2. Install the plugin:

   ```bash
   hermes plugins install https://github.com/trycua/hermes-cua-spaces
   ```

   Hermes asks to install one Python package (`cua`). Say yes, and say yes to enabling the plugin.

3. Point Hermes at it:

   ```bash
   hermes config set terminal.backend cua
   ```

Start Hermes as usual. The first command creates a Space (the first one takes a few minutes while Cua downloads the machine image). After that, every terminal command, file read, file write and code run lands in the Space.

Check it worked by asking Hermes: `run hostname and cat /etc/os-release`. You will see the Space's name and Ubuntu, not your own computer. On a Windows Space ask for `hostname` and `(Get-CimInstance Win32_OperatingSystem).Caption`.

## Settings

The plugin keeps its settings in Hermes's `config.yaml`, under `plugins.entries.cua-spaces.settings`. Set them with `hermes config set`.

| Setting | Default | What it does |
|---|---|---|
| `space` | `create` | `create` makes a Space for you. Or give the name or id of a Space you already have, for example `my-box`. |
| `on` | your Cua default | Where a new Space goes: `local`, `cloud`, `host:<machine>`, or the name of one of your machines. |
| `image` | `linux` | The image for a new Space. `linux` is Ubuntu with a desktop. `windows` makes a Windows Space (see [Windows Spaces](#windows-spaces)). |
| `desktop` | `false` | Install what Bot Desktop, `computer_use` and the browser need into a Linux Space that lacks it (see [The desktop stack](#the-desktop-stack)). |
| `delete_on_exit` | `true` | Delete a Space the plugin created when Hermes finishes. Set `false` to keep it for next time. A Space you named is never deleted. |
| `create_timeout` | `600` | Seconds to wait for a new Space to be ready. |

Examples:

```bash
# use a Space you already made
hermes config set plugins.entries.cua-spaces.settings.space my-box

# make new Spaces on another of your machines
hermes config set plugins.entries.cua-spaces.settings.on "spare mac mini"
```

The environment variables `HERMES_CUA_SPACE`, `HERMES_CUA_ON`, `HERMES_CUA_IMAGE` and `HERMES_CUA_DESKTOP` override the same settings for one shell.

How the plugin picks a Space:

- `space: create` (the default): each Hermes run makes its own Space, named `hermes-<task>-<random>`, does the work there and deletes it when it finishes. Two Hermes runs at once get two Spaces and never touch each other's files.
- `space: create` and `delete_on_exit: false`: the plugin makes one Space called `hermes-default` the first time and reuses it after that, so your files are still there next session. It stays running when Hermes exits. Stop it with `cua spaces stop hermes-default` or delete it with `cua spaces delete hermes-default`.
- `space: my-box`: the plugin uses that Space and never deletes or stops it. Because it is your machine and not a throwaway one, Hermes keeps asking before risky commands.

### Sign-in and keys

The plugin never stores a token or key. It talks to Cua through the `cua` Python package, which uses the sign-in that `cua auth login` saved. Nothing about Cua goes in `config.yaml` or `.env`.

### Working folder

Leave `terminal.cwd` alone. A new session starts in the Space user's home folder (`/home/cua` on Linux, `C:\Users\<name>` on Windows), and a `cd` below it is remembered for the next command and for the file tools' relative paths. This needs a Hermes that includes [NousResearch/hermes-agent#142225](https://github.com/NousResearch/hermes-agent/pull/142225) (newer than v0.22.2). Older Hermes versions send `/root` as their generic start folder; the plugin treats that as "no folder set" and starts in the home folder too.

## Watch the Space live

Open the Cua Spaces app and click the Space (`hermes-default`, or the one you named). You see its desktop as Hermes works. In a terminal, `cua spaces ls` lists your Spaces and `cua viewer` serves a browser viewer for all of them. You can click in the viewer to take over; Hermes keeps working next to you.

## Run several in parallel

Give each Hermes its own Space.

- Separate Hermes profiles, each with its own Space:

  ```bash
  HERMES_CUA_SPACE=build-a hermes --profile a
  HERMES_CUA_SPACE=build-b hermes --profile b
  ```

  `build-a` and `build-b` must exist (create them with `cua spaces create linux --name build-a`).

- Throwaway Spaces, one per run: this is the default. Every Hermes you start makes its own Space and deletes it when it finishes, so you can start as many as your machine can hold.

## Two other ways to use Cua with Hermes

These work today and need no plugin.

**1. Give Hermes the Cua tools (`cua mcp`).** Hermes can then create Spaces, run commands in them, drive their desktops and delete them, as tools it calls itself. Add this to `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  cua:
    command: cua
    args: ["mcp"]
```

Limit what Hermes may do with `--permissions` (for example `args: ["mcp", "--permissions", "spaces:readonly"]`) and with `tools.include` in the same block.

**2. Run Hermes itself inside a Space.** Everything stays in the Space: the agent, its keys and its tools.

```bash
cua agent create ada --harness hermes --in local:dev --env-from-host OPENAI_API_KEY
cua agent tell ada "summarize the repo in /home/cua/project"
```

Hermes keeps its memory in your Cua Volume, so it survives if the Space is replaced.

## Windows Spaces

Point the plugin at a Windows Space you have, or make one:

```bash
cua spaces create windows --name my-pc
hermes config set plugins.entries.cua-spaces.settings.space my-pc
# or let the plugin make one for each run
hermes config set plugins.entries.cua-spaces.settings.image windows
```

The first Windows Space takes a while to create while Cua downloads the machine image. A Windows Space on an Apple silicon Mac runs by emulation and is slow; use a Windows Space on an x86 machine or in your cloud account for real work.

Hermes runs on Windows through Git Bash, and the plugin does the same. **A Windows Space needs Git for Windows.** The Cua Windows image has it. If a Windows Space does not, the plugin installs it the first time Hermes connects (the official installer, checked against its SHA-256, with `winget` as a fallback), and shows a clear error naming the fix if it cannot, for example when the Space has no internet: install it yourself with `winget install --id Git.Git -e` in the Space's PowerShell, then start Hermes again.

| Hermes tool | On a Windows Space |
|---|---|
| `terminal` | Runs in PowerShell: `pwsh` (PowerShell 7) if the Space has it, otherwise the `powershell.exe` that ships with Windows. Write PowerShell, not bash: `Get-ChildItem`, not `ls -la`. Hermes tells the model this. The folder you `cd` into is remembered between commands. A command's exit code is the last command's: a failing program's own exit code, `1` for a PowerShell error, or the number in `exit N`. Output is UTF-8. |
| File tools (read, write, patch, delete, move), file search, background processes, `execute_code` | Hermes's own tools, unchanged, running in Git Bash. Name files the Git Bash way: `/c/Users/cua/app.py` for `C:\Users\cua\app.py` (Hermes tells the model). A relative path starts from the terminal's current folder. |
| Browser tools, `computer_use`, Bot Desktop | Not in a Windows Space: they use your own machine. |

Hermes also uses the Space's Python for deleting and moving files and for `execute_code`, so a Space without Python gets it installed the same way (the pinned python.org installer). To make `/c/Users/...` mean the same thing to bash and to Windows programs, the plugin adds a folder alias `C:\c` pointing at `C:\` (one per fixed drive). If Git for Windows cannot be installed the plugin stops with an error; if Python cannot, it logs a warning and only those features are unavailable.

The plugin never replaces or patches Hermes's own tools or modules. It uses only Hermes's public plugin interface for terminal backends.

If a Space cannot start PowerShell, the plugin says so and names the Space. Linux and macOS Spaces are unchanged.

## What does and does not run in the Space

| | In the Space with this plugin |
|---|---|
| `terminal` tool, background processes | yes |
| File tools (read, write, patch, search) | yes |
| `execute_code` | yes |
| Bot Desktop, `computer_use`, browser tools | yes in Linux Spaces, with a Hermes that includes [#142225](https://github.com/NousResearch/hermes-agent/pull/142225) (newer than v0.22.2), the `cua` command on your PATH (0.7.0 or newer, the first with `cua sandbox exec -i`) and the desktop stack in the Space (see below). Not in macOS or Windows Spaces, and not with `plugins.isolation: host` |
| The same on an older Hermes | no. They use your own machine, as before |

On a supported Hermes, `bot_desktop.placement: auto` puts the screen, `computer_use` and the browser in the Space, through `cua sandbox exec -i <space> -- bash -c ...`: a long-lived stream into the Space with stdin and stdout open, ended when Hermes stops. If the `cua` command is missing or too old, the plugin logs what to install and Hermes says the screen cannot follow the terminal; set `bot_desktop.placement: gateway` to run it on your machine instead.

### The desktop stack

The default `linux` image has an Xfce desktop, but Hermes starts its own display and browser in the Space and needs TigerVNC (`Xvnc`), Playwright Chromium, `agent-browser` and `cua-driver` there. Let the plugin install them once:

```bash
hermes config set plugins.entries.cua-spaces.settings.desktop true
```

The next Hermes run installs them into the Space it uses (a few minutes, needs internet and `sudo` in the Space, nothing happens if they are already there). With a Space you keep (`delete_on_exit: false`, or one you named) this happens once. To install by hand instead: `cua sandbox exec -i my-box -- bash -s < hermes_cua_spaces/desktop-stack.sh`. If the install fails the terminal and file tools still work and the log says what to do.

## What the plugin does and sends

- It calls the Cua SDK to create, find, start and delete Spaces, and to run commands and write files in them.
- With a local Space, nothing leaves your machine. With a Space on another machine or your cloud account, commands and files go through Cua's encrypted relay or your own network.
- It does not sync your Hermes skills, cache or credentials into the Space. The Space starts with what its image has.
- It adds no telemetry. The `cua` package has its own anonymous usage telemetry; turn it off with `cua telemetry off` or `CUA_TELEMETRY=0`.

## When something goes wrong

The plugin explains errors in plain words and says what to run next. The most common ones:

| You see | Do this |
|---|---|
| "Cua did not accept your sign-in" | `cua auth login` |
| "Cua can't find the Space ..." | `cua spaces ls`, then fix the `space` setting or use `create` |
| "Cua can't start a Space here yet" | `cua doctor` shows what is missing |
| "The `cua` Python package is not installed" | `hermes pm repair` |
| "The Windows Space ... has no Git for Windows, and the plugin could not install it" | Install Git for Windows in the Space (`winget install --id Git.Git -e`), check it has internet and admin rights, then start Hermes again |
| "Could not start PowerShell in the Windows Space ..." | Check the Space is running (`cua spaces ls`) and that `powershell.exe` runs in it |

## Develop

```bash
uv venv --python 3.14 && source .venv/bin/activate
# Hermes: clone a tag and install it editable. The PyPI "hermes-agent" is old and has no plugin API,
# and Hermes cannot be built into a wheel from git.
git clone --depth 1 --branch v0.22.5 https://github.com/NousResearch/hermes-agent.git ../hermes-agent   # latest release
# or, for the start-directory and Bot Desktop support: --branch main
pip install -e ../hermes-agent
pip install -e ".[test]"
pytest
hermes plugins validate . --install-deps
# Reproduce Hermes's plugin admission lock (uv lock across every platform Hermes supports, Android included):
python scripts/check_hermes_lock.py ../hermes-agent
```

The tests run the whole environment against a local shell standing in for a Space, so they need no Cua account. The Windows tests use PowerShell 7 (`pwsh`) as the stand-in and skip themselves when it is missing; on a Windows machine they run in real Windows PowerShell. CI runs them on Linux, macOS and Windows, against the latest Hermes release and against Hermes main, and also runs that admission lock so a dependency without wheels for a platform Hermes locks for (such as Android) cannot slip in. `cua` has no Android build, so it is declared with `sys_platform != 'android'`; this plugin does not run on Android.

## Licence

MIT, the same as the Cua SDKs. See [LICENSE](LICENSE).
