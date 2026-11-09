# Adding and configuring raid profiles

A raid is a JSON file in `profiles/`. The generic runner (`raids/base.py`) reads it, so most
raids, and most game updates, need no code. Edit profiles in the **Raids** page or by hand.

## Workflow for a new raid

1. **Raids → New**, enter the raid name.
2. In Roblox, open the screens you need. In **Testing → Capture template…**, drag around each UI element and name it. The PNG goes to `templates/`, and the region fractions go to your clipboard.
3. Fill in `detection` and the step lists in **Advanced (JSON)**. Paste regions from the clipboard.
4. In **Testing**, run **Test all detectors** on each screen. Every detector should say `FOUND` on its own screen and `no` elsewhere.
5. With dry run on, run **Entry steps**, **Restart steps**, **Run rotation** and then **One full raid**. Then repeat with dry run off.
6. Tick **Tested end-to-end** and save.

## Coordinates

Every `region` is `[x, y, width, height]`, given as fractions (0–1) of the Roblox client area.
`null` means the whole window. Templates are scaled by `window_height / template_reference_height`
(Settings) and are also tried at ±10%. Capture templates at the resolution you will play at, and set
the reference height to match.

## Matching speed

Matching runs at half resolution first, then re-scores the top 3 distinct candidates at full resolution, so a look-alike cannot hide the real element. After a
template matches once, only the scale that matched is tried until the resolution changes.

For each detector or template step:
- Give it a tight `region`. This is the biggest speed-up, because the cost grows with the area searched.
- Optionally add `"color": false` to match in grayscale. Grayscale is about 5× faster, but it can
  confuse elements that differ mainly in colour, such as a HUD and a reward banner of the same shape.
  Use it only on templates whose shape is unique. Then run **Test all detectors** on *every* screen and
  check that the template does not reach its threshold where it should not.

## Schema

| Key | Meaning |
|-----|---------|
| `id` | File name / unique id (auto from name). |
| `name` | Display name. |
| `module` | `generic`, or the name of a file `raids/<module>.py` that defines `RAID_CLASS`. |
| `verified` | Set this only after a full live run. Unverified profiles ask for confirmation before a live start. |
| `required_level` | Information only. |
| `difficulties` / `difficulty` | The list of options, and the selected one. `{difficulty}` in any template name is replaced with the selected one, for example `difficulty_{difficulty}` becomes `difficulty_Hard.png`. |
| `objective` | One of `player_exp`, `awakened_exp`, `material`, `crate` or `efficiency`. Used by **Recommend difficulty**. |
| `objective_target` | The item name for `material`/`crate`. It must match a key in `drops_per_run`. |
| `max_attempts` | Run cap for this profile (0 = none). Session limits still apply. |
| `run_timeout_s` | The fight fails after this many seconds. |
| `enter_timeout_s` | How long to wait for the raid HUD after the entry or respawn steps. |
| `entry_steps` | Run when you are not already in the raid. |
| `claim_steps` | Run after completion is detected (for example, clicking claim). |
| `restart_steps` | Run after every run, successful or failed (for example, clicking replay). |
| `respawn_steps` | Run when `death` is detected. If this list is empty, a death fails the run. |
| `detection` | See below. `in_raid` and `complete` are **required**. |
| `combat` | See below. |
| `reward_ocr` | `{"player_exp": [x,y,w,h], ...}`: digits are read from these regions on the reward screen. Needs Tesseract. |
| `rewards_per_run` | Values credited per successful run when OCR is off or fails. |
| `drops_per_run` | `{"Item": count}`, credited per successful run. |

### Steps

```json
{"action": "key", "key": "e", "hold": 0.0, "repeat": 1, "delay": 0.1}
{"action": "click", "pos": [0.5, 0.8]}
{"action": "click_template", "template": "replay_button", "region": null, "threshold": 0.8,
 "timeout": 10, "offset": [0, 0], "optional": false}
{"action": "wait_template", "template": "raid_hud", "timeout": 30}
{"action": "wait_gone", "template": "loading_screen", "timeout": 60}
{"action": "wait", "seconds": 2}
```

Every step accepts `"after": seconds` (default 0.2). If a non-optional template step times out, the
run fails with the best confidence it saw. The failure is logged, and a screenshot is saved to `logs/screens/`.

### Detectors

```json
"detection": {
  "in_raid":  {"template": "raid_hud", "region": [0.25, 0, 0.5, 0.2], "threshold": 0.8},
  "loading":  {"template": "loading_screen"},
  "boss":     {"template": "boss_choso", "region": [0.25, 0, 0.5, 0.12]},
  "complete": {"template": "reward_screen"},
  "failed":   {"template": "defeat_screen"},
  "death":    {"template": "death_screen"},
  "boss_health":   {"region": [0.3, 0.05, 0.4, 0.03], "color": [200, 40, 40], "tolerance": 45},
  "player_health": {"region": [...], "color": [r, g, b], "tolerance": 40}
}
```

Bars report the fraction of columns that match `color` (RGB). To get the colour, sample the bar from a
screenshot in any image editor.

- If `boss` is missing, the fight starts as soon as you are in the raid.
- If `boss_health` is missing, the no-progress recovery is disabled.

### Combat

```json
"combat": {
  "abilities": [{"name": "Skill 1", "key": "1", "cooldown": 8, "priority": 5, "hold": 0, "enabled": true}],
  "tick_interval": 0.1,
  "approach": {"key": "w", "seconds": 0.5},
  "lock_on_key": "",
  "low_health_pct": 0, "low_health_ability": "",
  "no_progress_timeout": 25, "recovery_steps": [{"action": "key", "key": "w", "hold": 0.6}],
  "max_recoveries": 3
}
```

On each tick, the engine presses the highest-priority enabled ability that is off cooldown.

- If boss HP does not change for `no_progress_timeout` seconds, the engine runs `recovery_steps`, then approaches again.
- If that happens more than `max_recoveries` times, the run fails.
- If the screen matches no known state for `unknown_state_timeout` (Settings), the **session stops**. It never continues blindly.

## Custom raid modules

When a raid needs logic that JSON cannot express, copy `raids/example_custom.py` and override hooks
(`enter`, `approach_boss`, `handle_death`, `fight`, `claim`, `restart`, `run_once`). Then set
`"module": "<file name>"`. Raids use only `self.rt` (vision, input, sleep, checkpoint), so stop and
pause keep working. Always wait with `self.rt.sleep()`, never `time.sleep()`.
