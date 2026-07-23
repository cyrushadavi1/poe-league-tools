# The really easy version (start here if this all looks scary)

Someone in your party sent you this folder. It puts a little helper
card on top of your game that tells you where to go next, step by
step, for the whole campaign. You don't have to read guides or
remember anything — the card updates by itself as you play.

It is **allowed by the game rules** (it only reads a text file the
game writes — same as every popular leveling tracker), and it
**cannot get you banned**. Nothing to worry about.

## Set it up (2 double-clicks, about 5 minutes)

**Step 1.** Open this folder and double-click **`setup_pc.bat`**.

- A setup window opens with a **Build / PoB** dropdown.
- Pick **Carry**, **Aurabot**, **Banner**, or **Drugger**.
- Type your exact Path of Exile character name.
- The Client.txt box is usually filled automatically. Click
  **Use this build**, then close the black window when setup finishes.

**Step 2.** Start Path of Exile and change one setting:

- Press **Esc** → **Options** → **Graphics**
- Find **Window Mode** and set it to **Windowed Fullscreen**
- (This is the only game setting you need to touch. Without it the
  helper card is invisible.)

**Step 3.** Back in this folder, open the **`overlay`** folder and
double-click **`run_overlay.bat`**.

- A small card appears on your screen. That's it — you're done.
- Drag it with your mouse to any spot you like.

Next time you play, you only ever do **Step 3**.

## How to use it while playing

You mostly don't. Just play, and:

- The card shows **what to do next**. When you finish those things
  and move to the next area, it changes by itself.
- Follow your friends and do what the card says. That's the whole
  system.

Four things worth knowing:

- **Card in the way?** Put your mouse on it and scroll the mouse
  wheel — it gets smaller. Double-click it to shrink it to one line.
- **Card showing the wrong step?** Press **F2** (go back one step) or
  **F3** (go forward one step) until it matches where you are.
- **Want it gone for a moment?** Press **F4**. Press **F4** again to
  bring it back.
- **Wondering if an item is good?** Point your mouse at the item in
  game and press **Ctrl+C**. The card tells you TAKE or SKIP.
- **Picked the wrong build?** Press **F10**, choose the correct PoB,
  and click **Use this build**. You can also double-click
  `choose_build.bat` when the overlay is closed.

A second panel with little maps may pop up when you enter a new
area. Click the picture that looks like your minimap, then follow
the green line to the exit. If you don't like the panel, press
**F7** and it goes away.

## Rather have it TALK to you?

The helper can also **read each step out loud** when you enter a new
area, so you never have to look away from the fight to read the card.

To turn it on: open the **`overlay`** folder, right-click
**`config.json`** → **Open with** → **Notepad**. Find the part that
says:

```
"narration": {
    "enabled": false,
```

Change `false` to `true`, save, and restart the overlay (Step 3).
That's it — it uses the voice built into Windows, nothing to install.

While playing: **F8** makes it repeat the current step (missed what
it said? press F8). **F9** turns the voice off and on. If it talks
too fast or too slow, change `"rate": 0` in the same spot (try `-2`
for slower, `2` for faster).

## Want to actually understand the game?

Read **`BEGINNER_LEVELING.md`** (in this same folder). It explains,
in plain words, everything the card will ask you to do — gems,
flasks, waypoints, what each act's boss does — plus an act-by-act
companion for the whole campaign. You don't need it to play, but
it makes everything make sense.

## If something doesn't work

1. Double-click **`doctor.bat`** in this folder.
2. Take a screenshot of what it says (press
   **Windows key + Shift + S**, drag over the window).
3. Send the screenshot to the group chat. Someone will tell you
   exactly what to click.

You cannot break anything by re-running `setup_pc.bat` or
`doctor.bat` — they are always safe to double-click again.
