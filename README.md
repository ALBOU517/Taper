# Taper

[![Live demo](https://img.shields.io/badge/live%20demo-taper--rpfr.onrender.com-e8590c?style=for-the-badge)](https://taper-rpfr.onrender.com)
[![CI](https://github.com/ALBOU517/Taper/actions/workflows/ci.yml/badge.svg)](https://github.com/ALBOU517/Taper/actions)
![Python](https://img.shields.io/badge/python-3.12-3776ab) ![License](https://img.shields.io/badge/license-MIT-green)

### 👉 [Try it live: taper-rpfr.onrender.com](https://taper-rpfr.onrender.com)

Create a free account (no credit card, takes a minute) and the setup wizard builds you a plan.
It's a small free demo server: if nobody has used it for a while, the **first load can take up to a minute** to wake up. Please don't enter anything you consider private; to keep your data on your own machine, [run it yourself](#quick-start).

**The planner for hybrid athletes: train for the race, eat for the training.** Pick a race and a date. Taper builds the whole season *backwards from the finish line*, fits every session into the free time in your real calendar (classes, shifts, exams), and tells you each day how much to eat (calories, protein, carbs, fat) for the training you're about to do, plus when to eat it. Food logging uses public nutrition databases, so you don't need a separate calorie app.

![Today view](docs/screenshots/today.png)

| Pick only the sports you do | Dark theme, week view | Short on time? Change a session |
|---|---|---|
| ![Wizard](docs/screenshots/wizard-sports.png) | ![Week](docs/screenshots/week-dark.png) | ![Adapt](docs/screenshots/adapt-session.png) |

> Half marathon, marathon, 5K/10K, sprint/Olympic triathlon, 70.3, Ironman, or an open-ended general-fitness block. One app, any athletic goal.

---

## Features

| | |
|---|---|
| **Level ladders** | Every sport has stages from "never done it" to performance (run: walk-run → 5K → 10K → half → performance). A level check places you; each week Taper moves you up, holds, or steps you back from what you actually did, and rebuilds the plan. |
| **Training-aware nutrition** | Daily calories and macros = your expenditure + the energy of *that day's* sessions ± your goal (lose, recomp, maintain, gain). Deficit is moved off hard days. |
| **Fuel timing** | Before / during / after guidance per session, e.g. "20 g carbs every 20 min from minute 20" on a 2 h 20 run. Carb-loading before long races. |
| **Food log** | Search the Canadian Nutrient File (French + English), Open Food Facts (barcodes) and USDA FoodData Central; quick add, custom foods, "same as yesterday". |
| **Recipes & your own foods** | Anything missing from the databases (a sauce packet, a bakery item) is saved to your account permanently. Build recipes from ingredients, with optional cooked weight. |
| **Your data, kept** | One data folder with the database, daily backups (30 kept) and a full JSON export / restore. Editing a food never rewrites past days. |
| **Adaptive expenditure** | Learns your real energy expenditure from logged intake and smoothed weight trend (water, glycogen and creatine noise filtered). |
| **Your real calendar** | Paste Google Calendar's secret iCal link: classes, work and study blocks are protected, sessions get a start time, exam weeks are lightened automatically. |
| **Today's goals** | An auto-filled checklist (sessions, protein, calories, weigh-in, pre-race meal) plus your own habits, a daily score and 14-day history. |
| **Your gym split** | Define Push / Pull / Legs (or anything) in the workout language; the planner rotates them into strength slots with their real duration. |
| **Reverse periodization** | Base → Build → Peak → Taper → Race, laid out backwards from race day with 3:1 (or 2:1 / 4:1) load/deload cycles. |
| **Unified load model** | Every swim, ride, run and barbell session becomes a Training Stress Score, then feeds a Banister fitness–fatigue model. |
| **Readiness score** | Live fitness (CTL), fatigue (ATL) and form (TSB), with ramp-rate injury warnings. |
| **Workout DSL** | Write `Run: Warmup 15m @ Z2 -> 5x(1km @ 4:15/km, Rest 90s) -> Cooldown 10m` and get a structured, interactive workout card. |
| **"Life happens" solver** | Miss a session and the rest of the week is re-planned without breaking any recovery rule. |
| **Weekly targets** | Distance, time and session goals per sport, with progress tracked as you log. |
| **Calendar feed + reminders** | A private `.ics` subscription puts every workout, with its full steps and a morning alarm, in Google/Apple/Outlook calendar. |
| **Strava sync** | OAuth2 + webhooks automatically tick off planned sessions when you upload an activity. |
| **Installable PWA** | Works offline with the last-synced plan (bad gym Wi-Fi), installs on your phone's home screen. |
| **Multi-tenant + self-hostable** | Sign-up/sign-in for a hosted instance, one-command `docker compose up` for self-hosting. |

---

## Quick start

```bash
# Local dev (SQLite, no setup). Needs Python 3.11+
git clone https://github.com/ALBOU517/Taper.git
cd Taper/backend
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
# open http://localhost:8000 and create an account
```

```bash
# Self-host with PostgreSQL
cp .env.example .env            # set SECRET_KEY
docker compose up -d
```

Tests and lint:

```bash
cd backend && pytest -q && ruff check app tests
```

### Deploy your own copy for free

The live demo runs on [Render](https://render.com) (free web service from the `Dockerfile`) with a free [Neon](https://neon.tech) Postgres database. Create the Neon project, then a Render *Web Service* from this repo (Docker, Free plan) with two environment variables: `SECRET_KEY` (any long random string) and `DATABASE_URL` (the Neon connection string, `postgresql://user:password@host/db?sslmode=require`). The free web service sleeps when idle; the database is what keeps your data.

New accounts go through a short setup (body, goal, today's weight, calendar). Nothing is estimated until you enter it.

For a portfolio demo, set `DEMO_MODE=1` to show three simulated athletes on the sign-in page (**Maya**, half-marathon beginner; **Léo**, Olympic triathlon build; **Sam**, Ironman). Their history is simulated and clearly labelled; it's off by default.

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | Signs session tokens (required in production) |
| `DATABASE_URL` | Postgres in Docker, SQLite by default |
| `USDA_API_KEY` | Free key from FoodData Central (`DEMO_KEY` is heavily rate-limited) |
| `OFF_CONTACT` | Email or URL sent in the User-Agent, as Open Food Facts asks |
| `DEMO_MODE` | `1` shows demo athletes |
| `STRAVA_*`, `PUBLIC_URL` | Optional Strava sync |

---

## How it works

```
backend/app/
├── engine/               ← pure Python, no web/DB imports, fully unit-tested
│   ├── dsl.py            workout language → syntax tree
│   ├── load.py           TSS estimation + Banister model + readiness
│   ├── periodization.py  macrocycle, load targets, weekly scheduler
│   ├── constraints.py    recovery rules shared by planner and rebalancer
│   ├── rebalancer.py     missed-workout solver
│   ├── templates.py      session library (zones / %FTP → portable plans)
│   ├── nutrition.py      expenditure, daily targets, macros, fueling, weight trend
│   ├── calendar.py       iCal → busy blocks → free windows, exam detection, time slots
│   └── ics.py            RFC 5545 calendar feed
├── foods.py              Canadian Nutrient File, Open Food Facts, USDA clients
├── life.py               nutrition / food log / checklist / calendar services
├── api/                  FastAPI routes + Strava OAuth/webhooks
├── services.py           glue between engine and database
└── db.py                 SQLAlchemy 2.0 models (SQLite or Postgres)
frontend/                 Vue 3 PWA, no build step, vendored assets
```

The engine is deliberately free of framework code so the interesting parts can be read, tested and reused on their own.

### 1. One number for four sports: Training Stress Score

A pool swim, a ride, a run and a squat session share no units. Each is normalised by **intensity factor** (IF), effort relative to the athlete's threshold:

| Signal | Intensity factor |
|---|---|
| Bike power | `IF = power / FTP` |
| Run pace | `IF = threshold_pace / pace` |
| Swim pace | `IF = CSS / pace` |
| Heart-rate zone | Z1–Z5 → 0.55, 0.70, 0.83, 0.94, 1.05 |
| RPE (gym, or no sensor) | `IF = 0.40 + 0.06·RPE` |

```
TSS  = hours × IF² × 100      run, bike, gym
sTSS = hours × IF³ × 100      swim (water drag scales ~cubically with speed)
```

One hour at threshold is exactly 100 TSS in any sport, which is what makes multi-sport weeks comparable.

### 2. Fitness, fatigue and form (Banister impulse-response)

Daily TSS drives two exponentially weighted averages:

```
CTL_t = CTL_{t-1} + (TSS_t − CTL_{t-1}) · (1 − e^(−1/42))     fitness, 42-day time constant
ATL_t = ATL_{t-1} + (TSS_t − ATL_{t-1}) · (1 − e^(−1/7))      fatigue, 7-day time constant
TSB_t = CTL_{t-1} − ATL_{t-1}                                 form
```

Past days use logged TSS; future days use the plan, so the chart shows *where you'll be on race day* (dashed).

![Season view](docs/screenshots/season.png)

Readiness is classified from form and the 7-day CTL ramp:

| Form (TSB) | Status |
|---|---|
| ramp > 8 CTL/week, or TSB < −30 | Overreaching: injury risk |
| −30 to −10 | Building: productive overload |
| −10 to +5 | Balanced |
| +5 to +25 | Fresh: race-ready |
| > +25 | Detraining |

### 3. Reverse periodization

Phases are laid out **backwards** from race week: taper length depends on the event (1 week for a 5K, 3 for an Ironman), then peak, then the remaining weeks split ~55/45 into base and build, with every *n*th week a deload.

Weekly load targets come straight from the Banister model. To move fitness from `CTL₀` to `CTL₁` in one week of constant daily load `T`:

```
CTL₁ = T + (CTL₀ − T)·e^(−7/42)
⇒ T  = (CTL₁ − CTL₀·e^(−7/42)) / (1 − e^(−7/42))         weekly TSS = 7T
```

Deload weeks *lose* fitness, so a naive linear ramp never reaches the target. Instead, the per-week CTL step is **solved by bisection** so fitness lands on the event's peak target, capped at a safe ramp for the athlete's level (4 / 5 / 6 CTL per week).

### 4. Scheduling under recovery constraints

Each week's sessions (long, key, easy, strength) are placed on days in two passes:

1. **Key sessions**: a bounded depth-first search over day assignments finds the layout that keeps hard days furthest apart and puts long sessions on the longest available day.
2. **Everything else**: greedy placement by best fit; if nothing fits, the session is shortened, and only then skipped. Every skip is reported to the athlete.

Rules (in `constraints.py`, shared with the rebalancer):

- Never exceed a day's available time (+10% grace) or 2 sessions per day.
- No back-to-back hard days in the same sport, **including across week boundaries**.
- No heavy leg day the day before a key run or ride.
- No hard session the day before the race.
- A hard swim may share a day with hard land work (a common triathlon double); two hard land sessions may not.

Then a **fixed-point calibration** loop regenerates the week until the TSS of the *actual scheduled workouts* is within 8% of the target.

### 5. The "life happens" solver

When a session is missed:

1. **Key session?** Try to move it to a later day where every rule still holds, swapping out an easy session if needed.
2. **Can't move it?** Redistribute volume onto remaining easy/long sessions of the same sport: at most +25% each and never past a day's availability.
3. Recover **at most 70%** of the missed load. Chasing lost volume is how athletes get hurt; later weeks absorb the rest.

![Rebalancing a missed session](docs/screenshots/rebalance.png)

### 6. The workout DSL

```
Run:  Warmup 15m @ Z2 -> 5x(1km @ 4:15/km, Rest 90s) -> Cooldown 10m
Swim: 400m Easy -> 8x100m Freestyle @ 1:45/100m (20s rest) -> 200m Pull Buoy
Bike: Warmup 15min @ Z2 -> 3x(12min @ 90%FTP, Rest 5min) -> Cooldown 10min
Gym:  Back Squat 4x6 @ RPE 8 | Superset: Romanian Deadlift 3x10 + Hanging Leg Raises 3x15
```

A hand-written recursive-descent parser (`engine/dsl.py`) produces a tree of `Step`, `Repeat`, `Exercise` and `Superset` nodes. Parentheses-aware splitting handles nested repeats, and errors point at the offending fragment ("Unknown target '@ Z9'. Use Z1–Z5, 4:15/km, …"). The same tree drives the UI cards, the intensity profile, the TSS estimate and the calendar description.

![Workout builder](docs/screenshots/builder.png)

![Food log](docs/screenshots/food.png)

![Recipes](docs/screenshots/myfoods.png)

---

### 7. From never-done-it to race-ready: level ladders

Each sport has an ordered ladder (`engine/ladder.py`). A stage caps the longest session, the long session and the number of sessions per week, says whether intensity work is allowed, and can swap in a different session format:

| Sport | Stages |
|---|---|
| Run | First steps (1-min run / 2-min walk) → Run-walk → Continuous runner → 5K → 10K → Half-marathon → Performance |
| Swim | Water confidence (kick, drills) → Lengths (25 m repeats) → 100s → Continuous 400 → Open-water ready → Performance |
| Bike | Getting comfortable → Endurance base → Club rider → Strong rider → Performance |
| Strength | Learn the movements → Foundation → Strength → Advanced |

**Placement.** Unmeasured sports get a level check in week 1 (20-min run at RPE 6, 400 m swim, 30-min ride; strength is a question about lifting experience). The result places you on the ladder and, where justified, sets training zones: run threshold ≈ check pace ÷ 1.10 (÷ 1.03 if it was hard), CSS from the 400 m pace, FTP = 95 % of a hard 20-min power.

**Progression.** Every finished week is reviewed per sport: ≥ 75 % of sessions done with easy sessions at RPE ≤ 6 → progress up; < 50 % done or easy sessions at RPE ≥ 8 → step back; otherwise hold. Crossing a stage boundary rebuilds the plan from the new level.

**Projection.** The season is laid out assuming nominal progress, and the goal shows whether you're on track: e.g. never-ran → half marathon in 34 weeks reaches "Half-marathon runner" around week 25 (3 run-walks a week to start, long runs up to ~2 h 10 before the taper).

### 8. Eating for the training

Every formula is cited in `engine/nutrition.py`.

| Piece | Method | Source |
|---|---|---|
| Resting expenditure | Mifflin-St Jeor | Mifflin et al., *AJCN* 1990 |
| Daily life (no training) | BMR × 1.20 / 1.35 / 1.50 to start, then learned from your data | design choice, replaced by adaptive estimate |
| Running | net ≈ 1 kcal per kg per km | ACSM running equation |
| Cycling | work (kJ) ÷ 4.184 ÷ 24 % efficiency − resting | Coyle 1992; Ettema & Lorås 2009 |
| Swimming | METs 5.8–9.8 | Compendium of Physical Activities (2011) |
| Lifting | 3.5 MET typical split (02054), 5.0 heavy squat/deadlift day (02052), 6.0 if logged RPE ≥ 9 (02050) | Compendium of Physical Activities (2011) |
| Body-mass change | 7,700 kcal per kg | Hall, *Int J Obes* 2008 |
| Carbs by training load | 3–5 / 5–7 / 6–10 / 8–12 g/kg | ACSM/AND/DC position stand 2016 |
| Carbs during exercise | 0 / mouth rinse / 30 / 60 / 90 g/h by duration | Jeukendrup, *Sports Med* 2014 |
| Protein | 1.6–2.0 g/kg default, user-set | ISSN position stand 2017 |

```
target(day) = base + training_kcal(day) + goal_adjust(day)

goal_adjust: weekly total = rate × weight × 7700, spread with weights 1 / (1 + training_kcal / 300),
             so a long-run day carries little or no deficit and a rest day carries most of it.
floor:       never below resting expenditure + that day's training.
```

**Adaptive expenditure.** Weight is smoothed with an exponential moving average (α = 0.1). Over the last 28 days:

```
expenditure = mean(intake on days marked complete) − Δtrend_weight × 7700 / days
base        = expenditure − mean(training energy)
```

It's blended with the formula in proportion to how many complete days you've logged (full trust after ~4 weeks, capped at 90 %).

### 9. Your calendar

Google Calendar's *secret address in iCal format* is fetched server-side (https only; private/loopback addresses are blocked to prevent SSRF). Recurring events are expanded (RRULE, EXDATE, moved instances), converted to your time zone, padded with a travel buffer, and subtracted from your training window. Each day's free minutes **and longest free window** go into the scheduler as hard constraints. Events titled like exams (`examen`, `intra`, `final`, `midterm`…) mark that week, and the one before an early-week exam, as exam weeks: load × 0.6 and no hard sessions.

### 10. Food databases

| Source | Coverage | Licence |
|---|---|---|
| Canadian Nutrient File (Health Canada) | ~5,700 generic foods, French + English, lab-analysed | Open Government Licence – Canada |
| Open Food Facts | 3 M+ packaged products, barcodes | ODbL (attribution in the UI) |
| USDA FoodData Central | Foundation, SR Legacy and branded foods | Public domain |

All values are normalised per 100 g. **Missing nutrients stay empty and the food is flagged incomplete; nothing is guessed.** Logged entries snapshot their nutrients so history never changes. Open Food Facts allows ~10 searches per minute, so searches are cached for 24 h and rate-limited client-side; any source that's down is reported in the UI while the others still return results.

### 11. How accurate is "run 5 km, eat X more"?

The extra energy of a run depends mostly on **body mass × distance**, not pace:

| Factor | Effect | What Taper does |
|---|---|---|
| Body mass | Cost scales ~linearly with kg | Uses your current *trend* weight |
| Distance | Net cost ≈ 1 kcal/kg/km, nearly independent of speed on flat ground | Uses planned or logged distance |
| Running economy (technique, training, physiology) | People differ by a few to ~10 % at the same mass | Absorbed by the adaptive expenditure |
| Hills, wind, surface | Uphill raises cost substantially | Not modelled per session (yet) |
| Fitness | Trained runners are somewhat more economical; the bigger effect is that fitter people run *farther*, which the distance term captures | Indirect |
| Compensation | Across 1,754 adults measured with doubly labelled water, only ~72 % of extra activity energy showed up in daily expenditure (Careau et al., *Curr Biol* 2021) | The adaptive estimate learns your net effect from intake + weight trend |

Other estimates are less precise: resting equations are within 10 % for ~80 % of non-obese adults (Frankenfield 2005); gym, swim and unpowered bike energy come from population-average MET values (±20–30 %); wrist wearables were also found to be poor at energy expenditure (Shcherbina et al. 2017). That is why the formulas are only the **prior**: after two weeks of complete logs, Taper corrects your non-training expenditure from your own data, so systematic errors in the session estimates are absorbed into it. What stays formula-driven is the **day-to-day split**, which is the part sports-nutrition guidelines care about (fuel hard days, keep the deficit on easy days).

### 12. Your data

```
~/.taper/taper.db        everything you log, forever (SQLite)
~/.taper/secret.key      keeps you signed in across restarts
~/.taper/backups/        one consistent copy per day, last 30 kept
```

Set `TAPER_DATA_DIR` to move it (e.g. into a synced folder). Foods and recipes you create stay until you remove them; removing only hides them from search, and every logged entry keeps a snapshot of its nutrients. **Settings → Your data** downloads a full JSON export and restores one. New features add columns with a small forward-only migration at startup, so upgrading never loses history.

### 13. Setting a goal: one question per screen

1. **What do you want to train?** Running, cycling, swimming, strength, walking: one or several.
2. **A race?** Only races your picks can do are offered (a triathlon needs swim + bike + run); otherwise a rolling 8–24-week block. Run races take an optional goal time, turned into a goal pace (Riegel's model) and a realism check once your level is measured.
3. **Your body?** Lose fat, recomposition, maintain, build muscle; a goal weight and date become a safe weekly rate (capped at −1 % / +0.5 % of body weight per week).
4. **When can you train?** Your Google Calendar if connected, otherwise minutes per day, plus how much you train now. Level checks in week 1 replace "what's your level?".
5. **Preview**, then start.

### 14. Scenario bench

Real use cases live in `backend/scenarios/*.json`: a story, the inputs and what a good plan must look like. `python -m app.scenarios` prints a readable report (checks + sample weeks); `pytest` runs them too. Every scenario is also checked against universal rules: sessions fit the day's free time, workouts are valid, no second session of a sport on its key day, beginners get rest days, level checks come first. Adding a use case = adding one JSON file.

### 15. Real-life constraints

Plans have to survive real weeks. Added after testing against 20 written use cases (`backend/scenarios/`):

| Situation | What Taper does |
|---|---|
| **Equipment.** You train at home or have nothing | Strength sessions are rewritten exercise by exercise (`SWAPS` in `templates.py`): back squat → goblet squat → split squat. Effort is kept through RPE. |
| **Travel.** You're away from your gym on some days | Those weekdays get the no-equipment version. Any single session can also be switched with **Change for today → No equipment**. |
| **Short on time.** Only 15–30 min today | Strength sets drop to 2 and straight sets become supersets. Exercises are dropped only as a last resort. Endurance sessions are rebuilt shorter. Every change can be undone. |
| **Mobility as a sport** | 20-min routines (spine & hips, balance, core & glutes) written with timed holds (`Side Plank 3x30s`). They never count as a hard day or toward the two-sessions-a-day cap. |
| **Training you already do** (club, climbing, fight practice) | Its days get no hard Taper session, and there are no heavy legs the day before a hard one. Its calories count in your targets (Compendium "sports, general", 5–8 MET). |
| **Night shifts.** Found automatically in your Google Calendar | A 6 h+ event that crosses midnight and ends before noon marks the next day as a recovery day: no intervals, no heavy lifting. |
| **Strength-led plans** | Deload weeks keep every lifting day at ~60 % of the sets, 2 RPE lighter. Your own routines run in the order you wrote them. |
| **Long-course races** | Long rides take ~50–55 % of the week's riding (Ironman, 70.3, gran fondo). |

Run the bench with `cd backend && python -m app.scenarios`. Every plan is checked against universal rules plus each scenario's own expectations.

## API

Interactive docs at `/docs` (OpenAPI). Highlights:

| Method | Path | |
|---|---|---|
| `POST` | `/api/plans/preview` | Dry-run the generator (powers the live goal wizard) |
| `POST` | `/api/plans` | Generate and save a season |
| `GET` | `/api/today` | Today's sessions, weekly progress and the reminder text |
| `POST` | `/api/sessions/{id}/complete` | Log with time / distance / RPE / HR / power |
| `POST` | `/api/sessions/{id}/miss` | Mark missed and re-solve the week; returns the diff |
| `POST` | `/api/dsl/parse` | Parse and estimate a workout |
| `GET` | `/api/load` | Daily CTL / ATL / TSB series + readiness |
| `GET` | `/api/calendar/{token}.ics` | Private calendar subscription (timed events + reminders) |
| `GET` | `/api/nutrition/day` | Today's and tomorrow's targets, eaten totals, fuel plans, expenditure |
| `GET` | `/api/foods/search` | Merged search across the three databases + your foods |
| `GET` | `/api/foods/barcode/{code}` | Open Food Facts barcode lookup |
| `POST` | `/api/log` | Log a food by grams, or a quick-add with totals |
| `PUT` | `/api/weights` | Daily weigh-in |
| `GET` | `/api/checklist` | Today's goals with score and 14-day history |
| `POST` | `/api/calendars` | Connect an iCal feed |
| `POST` | `/api/plans/replan` | Rebuild from today around the current calendar and fitness |
| `GET` | `/api/levels` | Ladder per sport, current stage, weekly reviews, goal projection |
| `POST` | `/api/levels/{sport}/assess` | Log a level check → placement + re-plan |
| `POST` | `/api/recipes` | Create a recipe from ingredients (per-serving and per-100 g values) |
| `GET` | `/api/export` · `POST` `/api/import` | Full account backup and restore |
| `POST` | `/api/strava/webhook` | Strava activity events |

### Strava

Set `STRAVA_CLIENT_ID`, `STRAVA_CLIENT_SECRET` and `PUBLIC_URL`, then register `PUBLIC_URL/api/strava/webhook` as the subscription callback. Activities are matched to the planned session for that day and sport; unmatched ones are logged as extra sessions. To stay within Strava's API terms, only derived metrics (duration, distance, TSS) are stored, raw payloads are never persisted, and Strava data is never sent to third-party AI services. Ingestion is idempotent on webhook retries.

---

## Mobile

<p>
  <img src="docs/screenshots/mobile-today.png" width="300" alt="Today on mobile">
  <img src="docs/screenshots/mobile-week.png" width="300" alt="Week on mobile">
</p>

## Roadmap

- MacroFactor / CSV import of past weight and intake to seed the adaptive expenditure on day one
- iPhone Shortcut to log from the lock screen; barcode scanning on iOS (Safari lacks `BarcodeDetector`) via a WASM decoder
- French interface (food names are already bilingual through the Canadian Nutrient File)
- `.FIT` workout export for Garmin / Coros / Wahoo (the DSL tree maps 1:1 onto FIT workout steps)
- Web Push (VAPID) for server-scheduled reminders
- Auto-detect thresholds from Strava best efforts
- Per-athlete fitness/fatigue constants fitted from their own history

## Notes

TSS, CTL/ATL/TSB and the impulse-response model come from published sports-science literature (Banister 1975; Coggan's power-based TSS); nutrition follows the sources in section 7. These are evidence-based defaults for healthy adults, not medical or dietetic advice.

MIT License.
