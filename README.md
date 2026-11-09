# JJZ Raid Farmer

A desktop raid-farming macro for the Roblox game **Jujutsu Zero**. It is screen-based: it sees the
game through screenshots (OpenCV template matching, optional OCR) and plays it through ordinary
keyboard and mouse input. There is no injection, memory access, executors or remote events.

> **Status:** The engine, GUI, safety system and statistics are built and covered by automated tests
> against a simulated game. **The raid profiles have not yet been verified against the live game.**
> UI templates must be captured on your own machine. See [Limitations](#limitations).

## Features

- **Dashboard:** start, pause and stop; live state; current raid, difficulty and objective; session time;
  runs; EXP, Awakened EXP, Yen and Lumen (with per-hour rates); boss HP; and a multi-raid session queue.
- **Raid profiles:** one JSON file per raid. Each sets the difficulty, objective, abilities, timings,
  detectors, entry, claim, restart and respawn steps, retry limits and expected rewards. You can edit
  them in the GUI, and duplicate or delete them.
- **Combat:** a priority and cooldown rotation, attack interval, approach and lock-on, low-HP ability,
  and a "boss HP not changing" recovery step.
- **Statistics:** run history in `logs/history.jsonl`, with per-raid and per-difficulty success rate,
  average clear time, runs per hour, rewards and drops per hour. **Recommend difficulty** ranks the
  difficulties by your measured objective per hour.
- **Testing mode:** screenshots, template capture by drag-select, detector confidence table, key press,
  rotation test, and each step group on its own. You can also run one full raid.
- **Safety:**
  - Dry run is on by default.
  - Global emergency stop (**F8**) and pause (**F7**). The stop releases all keys immediately.
  - Maximum runtime and maximum consecutive failures.
  - Retry delay.
  - Unknown-state timeout: the macro stops rather than guessing.
  - Window focus check.
  - Screenshot saved on every error.
  - Rotating log in `logs/macro.log`.

## Install (Windows)

1. Install Python 3.10+ from python.org and tick **Add python.exe to PATH**.
2. Optional, for reading the reward screen: install Tesseract OCR (the UB-Mannheim build). Then
   either set its path in Settings or put it on PATH.
3. Double-click `run.bat`. The first launch creates `.venv` and installs `requirements.txt`.

Success: the window opens with an orange **DRY RUN** banner. If setup fails, delete `.venv` and run
`run.bat` again. Check `logs/macro.log` for errors.

Manual install:

```bash
py -3 -m venv .venv
```

```bash
.venv\Scripts\python -m pip install -r requirements.txt
```

```bash
.venv\Scripts\python main.py
```

## First-time setup (do this per raid)

1. Run Roblox at the resolution you will farm at. Windowed or fullscreen both work, but the window
   must not be minimised. In **Settings**, set *Template reference height* to that height (for example, 1080).
2. In **Testing**, click **Capture screenshot** to confirm the macro can see the game.
3. Capture these templates with **Capture template…**:
   - The raid HUD → `raid_hud`
   - The reward screen → `reward_screen`
   - The replay/vote button → `replay_button`
   - The defeat and death screens → `defeat_screen`, `death_screen`
   - The loading screen → `loading_screen`
   - The boss name plate → `boss_<raid id>`
   - Lobby buttons → `raid_select_<id>`, `difficulty_Easy` (and the other difficulties), `start_button`

   The shipped profiles use these names. Rename them to suit your setup.
4. Sample your boss HP bar's colour and position, and set `detection.boss_health`.
5. In **Raids → Combat**, set your real key binds and cooldowns.
6. Click **Test all detectors** on each screen.
7. With dry run on, run the step groups, then **One full raid**.
8. Turn dry run off in Settings, and test again with your hand on **F8**.
9. Tick **Tested end-to-end** and save the profile.
10. On the Dashboard, tick the raids you want, set the runs, and press **Start**.

[docs/ADDING_RAIDS.md](docs/ADDING_RAIDS.md) has the full profile schema and explains how to add raids.

## Architecture

```
main.py            entry point, logging
gui/               PySide6 window, theme, template-capture dialog
automation/        engine (session/queue/limits), input backends, window detection, hotkeys
vision/            screen capture (mss), template matching + bar reading (OpenCV), OCR
raids/             profile schema/validation, generic data-driven Raid, module registry
combat/            priority/cooldown rotation
stats/             run records, per-hour summaries, difficulty recommendation
profiles/          one JSON per raid
templates/         your captured PNG templates
config/            settings (settings.json is created on first save)
logs/              macro.log, history.jsonl, screens/
```

The farming logic (`raids/`) never calls a library directly. It only uses `Runtime`, which supplies
vision, input, interruptible sleep and the pause/stop checkpoint. This keeps raids testable
against a fake game (`tests/test_core.py`), and it makes stop and pause instant everywhere.
The statistics package is named `stats/`, not `statistics/`, so that it does not shadow
Python's standard library.

Input on Windows uses `pydirectinput` (SendInput scan codes), because Roblox ignores virtual-key
events. On other operating systems the fallback is `pynput`, which is intended for development only.

## Research findings (November 2026)

No public InformaalFrog macro for Jujutsu Zero was found. The InformaalFrog TikTok account posts
macros for other Roblox games (Anime Vanguards and others). The GUI takes its *style* from that family
of tools (dark panel, status pill, big start/stop buttons) but copies nothing from them. Other public
JJZ macros (AutoHotkey, single raid, fixed timings) were the reason for the opposite design here:
state is detected, not timed.

Public raid data is sparse and contradictory. Each profile's `notes` records what was found:

| Profile | What sources say | Confidence |
|---|---|---|
| Eldest Womb (Choso) | Fast Awakened EXP farm; the boss spawns near the entry. One tester reports ~5.6k Awakened EXP per 5 min | Low–medium |
| Deadly Judge (Higuruma) | Source of the Judgement Scale (reported 3.5% drop). Level gate given as 300 or 500 | Low |
| Sorcerer Killer (Toji) | Easy 200+ / Normal 300+ / Hard 400+ / Nightmare 500+ (one source). Strengthened Crate II from Hard/Nightmare | Low |
| Disaster Flame (Jogo) | Volcanic Crate from Hard/Nightmare. Level gate given as 100 or 150 | Low |
| King of Curses (Sukuna) | Stained Crates (Stained Ring, Stained Talisman). Level gate disputed | Low |
| Zen'in Elite / Awakened Zen'in | Endgame, beyond level 1000 (March 2026) | Low |
| Awakened Lightning God | No current source; Kashimo assumed | None |
| Strongest of Today | No source found | None |
| Halloween event | Oct 2026: 250M EXP, 15 Hallow Crates and 50 Candy per clear (seasonal) | Medium (may be removed) |

The sources also say Nightmare allows 2 lives. Use `respawn_steps` for that.

Because of these gaps, every profile ships with `verified: false`, and the level gates, rewards and lobby
flow are left for you to confirm in game.

## Limitations

- **This was not tested against live Roblox.** The build environment has no Windows desktop and no game.
  The following *are* tested:
  - The vision code, on synthetic frames.
  - The engine: full cycle, failure limit, unknown-state stop, and an emergency stop under 1 s, against a simulated game.
  - Profile validation.
  - Statistics and recommendations.
  - A GUI smoke test.
- The default entry, claim and restart steps and template names are placeholders until you capture
  templates and confirm the in-game flow.
- Navigation in the open world (walking to a raid NPC) is not automated. Start the macro in the raid
  lobby or inside a raid. Replay buttons keep you there between runs.
- OCR needs Tesseract. Without it, the per-run reward values you configure are credited instead.
- **Roblox rules:** Roblox's Terms of Use restrict automation and third-party tools that give an
  advantage. Macro use may still be against the rules even without exploits, and the game's
  developers may ban for AFK farming. You use this at your own risk.

## Tests

```bash
python -m pytest -q tests
```
