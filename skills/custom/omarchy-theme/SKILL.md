---
name: omarchy-theme
description: >
  Create a new custom Omarchy theme version-controlled in the config-backup repo.
  Use when asked to create, add, or make a new Omarchy theme. Covers full palette
  authoring, all 25 theme files, video wallpaper via mpvpaper, and restore workflow.
---

# Omarchy Theme Creation Skill

Creates a fully custom Omarchy theme, version-controlled in the config-backup repo at
`~/dev/config-backup/`, and applies it live.

## Critical: Directory Naming

Theme directories **must be all-lowercase with hyphens** (e.g., `my-theme`, not `My-Theme`).

`omarchy theme set "My Theme"` resolves to `my-theme` internally. A mixed-case directory
causes "Theme does not exist" even though `omarchy theme list` shows it.

## Step 1 — Gather Requirements

Ask the user (or infer from context):
1. **Theme name** — will become the directory name, lowercased + spaces→hyphens
2. **Base theme** — recommend Ethereal if already in repo at `configs/.config/omarchy/current/theme/`; otherwise copy from `~/.local/share/omarchy/themes/<name>/`
3. **Color palette** — get hex values for at minimum: `background`, `foreground`, `accent`, and the 16 ANSI colors (`color0`–`color15`)
4. **Video wallpaper?** — if yes, ask for the file location

## Step 2 — Copy Base Theme

```bash
# From repo (Ethereal base — no system access needed)
cp -r ~/dev/config-backup/configs/.config/omarchy/current/theme \
       ~/dev/config-backup/configs/.config/omarchy/themes/<theme-name>

# From system (any other stock theme)
cp -r ~/.local/share/omarchy/themes/<base-name> \
       ~/dev/config-backup/configs/.config/omarchy/themes/<theme-name>
```

The repo path (`configs/.config/omarchy/themes/`) is what `restore.sh` pushes to
`~/.config/omarchy/themes/`. Both must use the lowercase-hyphen name.

## Step 3 — Update All Color Files

A theme has **25 files**. Every file with hardcoded colors must be updated. Use the
palette from Step 1.

### colors.toml — master palette reference
```toml
accent = "#XXXXXX"
cursor = "#XXXXXX"
foreground = "#XXXXXX"
background = "#XXXXXX"
selection_foreground = "#XXXXXX"
selection_background = "#XXXXXX"

color0  = "#XXXXXX"   # black
color1  = "#XXXXXX"   # red
color2  = "#XXXXXX"   # green
color3  = "#XXXXXX"   # yellow
color4  = "#XXXXXX"   # blue
color5  = "#XXXXXX"   # magenta
color6  = "#XXXXXX"   # cyan
color7  = "#XXXXXX"   # white
color8  = "#XXXXXX"   # bright black
color9  = "#XXXXXX"   # bright red
color10 = "#XXXXXX"   # bright green
color11 = "#XXXXXX"   # bright yellow
color12 = "#XXXXXX"   # bright blue
color13 = "#XXXXXX"   # bright magenta
color14 = "#XXXXXX"   # bright cyan
color15 = "#XXXXXX"   # bright white
```

### hyprland.conf — active window border
```bash
$activeBorderColor = rgb(XXXXXX)   # accent, no leading #
```

### Terminal configs — all use the same 16-color palette

| File | Format |
|------|--------|
| `ghostty.conf` | `background = #XXXXXX` / `palette = 0=#XXXXXX` |
| `alacritty.toml` | `[colors.primary]` / `[colors.normal]` / `[colors.bright]` sections |
| `foot.ini` | `[colors-dark]` section, colors without `#` prefix |
| `kitty.conf` | `foreground #XXXXXX` / `color0 #XXXXXX` (no `=`) |

### UI configs — background + foreground + accent only

| File | Key colors |
|------|-----------|
| `waybar.css` | `@define-color foreground` / `@define-color background` |
| `walker.css` | `@define-color selected-text` (accent) + foreground + background |
| `mako.ini` | `text-color` (fg) / `border-color` (accent) / `background-color` (bg) |
| `swayosd.css` | `@define-color background-color` / `border-color` / `progress` (accent) |
| `hyprlock.conf` | `rgba()` values — bg, inner (bg at 0.8 alpha), outer (fg), font (fg), check (accent) |

### helix.toml — only update the `[palette]` section at the bottom

The syntax highlighting rules use named aliases; only the `[palette]` block has hex values.

### btop.theme — semantic color assignments

Map palette colors to btop's semantic slots:
- `main_bg`/`main_fg` → background/foreground
- `hi_fg` → accent (cyan)
- `selected_bg` → color8 (bright black)
- `title`/`proc_misc` → color5 (magenta)
- Box outlines → color2 (green)
- Gradients → use cyan/blue/green for temperature, CPU, network

### gum.env.conf and obsidian.css — bulk sed replacement

Both files have many instances of the same ~4 ethereal colors. Use sed:

```bash
sed -i \
  -e 's/<old-fg>/<new-fg>/g' \
  -e 's/<old-bg>/<new-bg>/g' \
  -e 's/<old-accent>/<new-accent>/g' \
  -e 's/<old-dim>/<new-dim>/g' \
  <file>
# Verify: grep -c '<old-color>' <file> should return 0
```

### neovim.lua — colorscheme plugin

Change to a neovim colorscheme that matches the theme aesthetic. `tokyonight-night`
ships with LazyVim and suits dark/cool palettes. Keep `ethereal` only if intentionally
inheriting the ethereal neovim theme.

### vscode.json — extension name + ID

```json
{ "name": "Theme Display Name", "extension": "publisher.extension-id" }
```

### Files that need NO color changes

- `chromium.theme` — references a system Chrome theme; leave as-is
- `icons.theme` — icon pack name only
- `keyboard.rgb` — keyboard lighting; update only if intentionally changing RGB colors
- `preview.png` / `preview-unlock.png` / `unlock.png` — reuse base theme images as placeholders

## Step 4 — Video Wallpaper (optional)

If the user wants a video wallpaper:

1. **Move video** into the theme backgrounds directory:
   ```bash
   mv <video-file> ~/.config/omarchy/themes/<theme-name>/backgrounds/<theme-name>.mp4
   ```

2. **Create mpvpaper hook** at `~/.config/omarchy/hooks/theme-set.d/mpvpaper.sh`:
   ```bash
   #!/bin/bash

   VIDEO="$HOME/.config/omarchy/themes/<theme-name>/backgrounds/<theme-name>.mp4"

   if [[ "$1" == "<theme-name>" ]]; then
     pkill -x swaybg 2>/dev/null
     pkill -x mpvpaper 2>/dev/null
     setsid uwsm-app -- mpvpaper -o "no-audio loop" '*' "$VIDEO" >/dev/null 2>&1 &
   else
     pkill -x mpvpaper 2>/dev/null
   fi
   ```
   ```bash
   chmod +x ~/.config/omarchy/hooks/theme-set.d/mpvpaper.sh
   ```
   Hook receives the lowercased-hyphenated theme name as `$1`.

3. **Copy hook into repo** for backup:
   ```bash
   cp ~/.config/omarchy/hooks/theme-set.d/mpvpaper.sh \
      ~/dev/config-backup/configs/.config/omarchy/hooks/theme-set.d/mpvpaper.sh
   ```

4. **Add video extensions to .gitignore** (videos are too large for git):
   ```
   configs/.config/omarchy/themes/*/backgrounds/*.mp4
   configs/.config/omarchy/themes/*/backgrounds/*.mkv
   configs/.config/omarchy/themes/*/backgrounds/*.webm
   ```

## Step 5 — Static Backgrounds

Background images in `backgrounds/` are cycled by `omarchy theme bg next`. Naming:
- Prefix with a number to control cycle order: `1-ocean.jpg`, `2-city.jpg`
- Omit number for unordered entries (e.g., `omarchy.png` branding image)
- Formats: JPG and PNG are safe; avoid video formats (use mpvpaper hook instead)

## Step 6 — Restore and Apply

```bash
# Push theme from repo to live system
cd ~/dev/config-backup && ./restore.sh

# Verify theme is recognized
omarchy theme list   # should show new theme with display name (spaces, title-case)

# Apply
omarchy theme set "<Display Name>"

# Confirm
omarchy theme current
```

## Step 7 — Verify and Commit

Verify visually: terminal background/foreground, Waybar, Hyprland window borders, Btop, Helix.

Then commit:
```bash
cd ~/dev/config-backup
git add configs/.config/omarchy/themes/<theme-name>/ \
        configs/.config/omarchy/hooks/ \
        .gitignore
git commit -m "feat: add <Theme Name> custom theme"
```

## Background: How the Repo ↔ System Relationship Works

- `backup.sh` → copies `~/.config/omarchy/` → `configs/.config/omarchy/` (rsync --delete)
- `restore.sh` → copies `configs/.config/omarchy/` → `~/.config/omarchy/` (rsync --delete)
- New theme files go in repo first, then `restore.sh` pushes them live
- After verifying, run `backup.sh` before committing to capture any changes omarchy made
  during `theme set` (e.g., `current/theme.name`, `current/theme/` symlink targets)
