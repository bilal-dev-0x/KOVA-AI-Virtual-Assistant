# KOVA identity, personas, human-like voice and avatar: plan (not built yet)

Status: **plan only, nothing implemented.** Goes together with `docs/UI_DESIGN_PLAN.md`: the avatar lives in the new window.

## 0. What Bilal made (checked 7 Nov, `Downloads/`)
| File | What it is | Facts that matter |
|---|---|---|
| `Gemini_Generated_Image_...jfif` | **Mini Reaper** mascot: hooded, glowing cyan eyes, "M" on the cloth, small scythe, smoke tail | 1408x768, dark flat background, no transparency |
| `Firefly Animate this exact Mini Reaper...mp4` | 8 s idle loop of the same mascot: floats, cyan sparks swirl, eyes squint, glow burst, settles | 1920x1080, 24 fps, H.264, 8 MB, no transparency, starry dark background |
| `Futuristic Assistant Avatar Expression Atlas.png` | 32-cell sheet of an **anime-style human** avatar: male and female, 8 states each (idle, listening, thinking, speaking, happy, focused, sleepy, error), plus gaming / work / chill / serious / excited / sad / welcome / goodbye, plus UI icons (voice active, processing, task executing, approval needed, pill, floating logo, theme colours, avatar frame) | 1536x1024 total, so each cell is only about 190 px; captions are printed inside the cells |

Two different art directions exist now (mascot and anime humans). Section 4 decides how they fit together.

## 1. Identity: the final names
Today: product **KOVA**, assistant **Max**, server models `kova-cmd` (acts, tools) and `kova-chat` (talks).

Proposal, three layers so nothing breaks:
1. **Product:** KOVA (repo, window title, release, README). Final.
2. **Assistant name (what the user says and hears):** Max, kept as the default. Stored in `config.json` (`ASSISTANT_NAME`), so each persona can have its own.
3. **Model names:** stay technical and internal (`kova-cmd`, `kova-chat`). Users never see them except in the "brain" footer. Renaming would need new Ollama models built on the server for no visible gain, so the plan is **not** to rename them. Optional only: show friendly labels in the UI footer ("KOVA server · acts", "KOVA server · chats").

**Decided (7 Nov):** male persona **Max**, female persona **Mira**. Model names stay `kova-cmd` / `kova-chat` (internal, not renamed).

## 2. Two personas (male and female)
A persona is one small file, so adding a third later is easy: `personas/<id>.json`.

| Field | Max (male) | Female persona |
|---|---|---|
| name, wake words | Max, mac, mx | Mira + common mishearings (to be listed when built: mera, mira, meera) |
| TTS voice | existing edge-tts male | existing edge-tts female (check for Urdu voices in edge-tts and compare) |
| how they address the user | "Commander" only occasionally | same title, different taste |
| tone | calm, dry, short, a little witty | warm, quick, a little playful |
| signature acknowledgements | "On it." "Done." "Give me a second." | "Sure." "Got it." "One moment." |
| avatar set | atlas male row, mascot | atlas female row, mascot |

Where it plugs in (all already exist): `ASSISTANT_NAME`, `USER_TITLE`, `WAKE_WORDS`, `VOICE`, and the chat prompt of `kova-chat` (`brain/server.py` `chat_prompt`) plus the Gemini system prompt (`agent_loop.system_prompt`). A persona file only fills those. Switching: "talk like a girl" already changes the voice; it would now load the whole persona, saved in `config.json`. Settings screen gets a persona switch.

Rule: tools, safety, approvals and honesty are **identical** for both personas. Only wording, voice and looks differ.

## 3. Human-like (without pretending to be human)
Concrete, testable things, in order of value:
1. **Speed feel:** a short spoken or visual acknowledgement within about 1 s ("Hmm, let me check"), while the real answer is still being worked on. The server's chat model needs 5-10 s, so this matters most.
2. **No repeated phrases:** 4-6 variants for acknowledgements, done, error; never the same one twice in a row.
3. **Address the user less:** "Commander" at most once every few turns, not every sentence.
4. **Short spoken answers**, and Roman Urdu / English matched to what the user said (already built; keep it).
5. **Small-talk memory:** facts the user shares go through the existing `remember` tool (still asked first); the persona may refer to them naturally.
6. **Mood follows events** (never faked): happy after a finished job, concerned after an error, sleepy when asleep, focused while a tool runs. These drive the avatar in section 4.
7. **Interrupt Max mid-sentence** and natural streaming voice: that is the Gemini Live work from Day 6 Advanced, same plan, later.
8. **Honest identity:** if asked, Max says it is an assistant program. No claims of feelings, body or human experiences.

Out of scope: voice cloning, changing the TTS engine, anything that adds a model on this laptop.

## 4. Avatar
### 4.1 Which art goes where
**Update (7 Nov, Bilal):** the two files he made are the Reaper picture and the Reaper idle video. The Expression Atlas is not part of his set, so the plan now follows **Option C: Reaper only**. The atlas stays unused unless he says otherwise. Male and female personas then differ by name, voice and wording, and by an accent tint of the same Reaper (e.g. cyan for Max, a second colour for the female persona); there are no human portraits.

Earlier options, kept for reference:
- **Option A (recommended):** Mini Reaper = KOVA's face (logo, window icon, loading, floating pill, idle video on Home). Anime humans = optional "persona portraits" shown next to the chat, off by default.
- **Option B:** anime humans are the main avatar, the Reaper is only the app icon.
- **Option C:** Reaper only; no human portraits.

Honest note: the anime sheet looks clearly AI-generated, and Bilal asked for a UI that does not feel AI-built. The Reaper is more distinctive and fits that goal better, so A or C is the safer fit.

### 4.2 State machine (one place decides the avatar)
The avatar only mirrors real state; it never plays a state the assistant is not in.

| Real event | Avatar state |
|---|---|
| asleep (wake word needed) | sleepy |
| awake, waiting | idle |
| mic hears speech | listening |
| agent request running | thinking (processing ring) |
| a tool is running | focused / task executing |
| Max speaking | speaking (needs a new `speaking` flag from the TTS layer; today there is none) |
| approval pop-up open | approval needed |
| failed result | error / concerned |
| finished OK | happy, for 2 s |
| gaming mode on | gaming |
| app start / quit | welcome / goodbye |

### 4.3 Preparing the assets (done once with ffmpeg, which is installed)
- **Video:** crop to the mascot, scale to about 480-600 px, re-encode to a small WebM; target 1 MB instead of 8 MB. No alpha exists, so on the dark window it is blended in (`mix-blend-mode: screen` works because the background is almost black); for the round pill it is masked into a circle.
- **Mascot picture:** keep as PNG; remove the flat background with a luma key (smoke edges need a look). Used as logo, favicon, tray icon (several sizes).
- **Atlas:** cut each portrait out of the 32 cells (without the printed captions), save as separate small WebP files named by state. 190 px is enough for the pill and a chat avatar, **not** for a large hero. If a large one is wanted, Bilal regenerates only the states he needs at higher resolution.
- Originals stay in Downloads; only optimised files go into `assets/avatar/`. Target total under 5 MB in git.

### 4.4 Performance rules
- Video plays only while the dashboard is visible and Max is awake; paused and released when hidden or asleep (idle CPU stays near zero).
- The pill uses stills with a CSS fade, not video.
- At most one animated element on screen at a time.

## 5. Build order (after the UI plan's steps 1-2 exist)
1. **Personas (no UI needed):** persona files, loader, wiring into name / wake words / voice / both prompts, Settings switch, tests.
2. **Human-like layer:** acknowledgement variants, title frequency, speed-feel acknowledgement, tests that no phrase repeats twice in a row.
3. **Asset prep:** ffmpeg script `tools/prepare_avatar.py`, optimised files in `assets/avatar/`.
4. **Avatar state machine** (Python, tested) plus the `speaking` flag.
5. **Avatar in the UI:** Home hero, pill, tray icon, window icon.
6. **Live test with Bilal:** both personas, a few states, RAM and CPU measured with the avatar running.

## 6. Risks
- Two art styles side by side can look messy: settle 4.1 before building.
- A speaking state needs the TTS layer to report start and end; check `voice_handler.speak` first.
- Female voice quality in Roman Urdu is unknown until tried; test before promising it.
- The Gemini Live "natural voice" is separate and depends on free quota.
- Reaper video: if the dark background does not blend cleanly on the final theme, key it to alpha or keep it inside a dark rounded frame.

## 7. Decisions (all made, 7 Nov)
1. Female persona: **Mira**. Male: **Max**.
2. Avatar: **Reaper only** (option C), a second accent tint for Mira.
3. Model names: unchanged, internal.
4. Expression Atlas: not used.
5. Old CustomTkinter window kept as `--ui-classic`; new UI is dark only (light later).

Nothing is built yet: planning only.
