# Walkworth

*Walk it. Say it. Keep it.*

A spoken trek journal. You talk while you walk and your phone stays in your pocket. Each voice note and photo uploads as you go (or waits on the phone until you're back in range). Then Gemma writes the journal around your own words, and every line it adds cites the notes it came from. A line that cites nothing is dropped before the journal is made.

## How it works

1. **Start a walk.** One big button: tap, talk, tap. The phone buzzes when it listens, and each note gets a GPS fix.
2. **It uploads as you go.** Notes and photos are kept in the browser until the server has them, so a dropped signal or a closed tab loses nothing.
3. **Make the journal.** A Temporal workflow transcribes (ElevenLabs Scribe), lines notes up with the GPX track, picks out what you saw, captions photos and writes the connecting lines (Gemma). Review the words, then export HTML, PDF, GPX or Markdown.

## Run it

Needs Node 20+, Python 3.11+, [uv](https://docs.astral.sh/uv/), and the [Temporal CLI](https://docs.temporal.io/cli) in `tools/temporal/`.

```bash
cp .env.example .env   # add LLM_API_KEY (Google AI Studio) and ELEVENLABS_API_KEY
npm run setup          # install, build the fort pack and sample trek, generate types
npm run dev            # http://127.0.0.1:5173 (API :8000, Temporal :7233)
npm run worker         # in a second terminal: runs the pipeline
```

`npm test` runs the unit and API tests, the web typecheck and the build (no keys needed). `npm run e2e` runs the whole flow against a running server with the real APIs and saves logs and PDFs in `test/` (about 25 model calls).

### Docker Compose

Docker Compose runs the built app, Temporal, PostgreSQL, and the Temporal UI together:

```bash
docker compose up --build
```

Open the app at http://127.0.0.1:10001 and the Temporal UI at
http://127.0.0.1:7233. Temporal's host-facing gRPC endpoint is on port `7234`;
inside Compose, services continue to use `temporal:7233`. Stop the stack with
`docker compose down`.

## Deploy

One free Render web service from `render.yaml`. The Docker image runs the API, the web app, Temporal's dev server and the worker together.

1. Push this repo to GitHub, then in Render choose **New → Blueprint** and pick the repo.
2. Paste `LLM_API_KEY` and `ELEVENLABS_API_KEY` when asked.
3. Open `/api/health` on the new URL and check both keys show `true`.

The app pings its own `/api/health` every 5 minutes so the free instance stays awake; add an external monitor (UptimeRobot, cron-job.org) as a backup. Set `TEMPORAL_ADDRESS`, `TEMPORAL_NAMESPACE` and `TEMPORAL_API_KEY` to use Temporal Cloud instead of the in-container dev server.

**Storage is ephemeral.** Uploads reset on each deploy and the sample reloads on boot. If the server loses a walk mid-trail, the phone starts it again and re-uploads everything it still holds.

## Sharing and privacy

There are no accounts. The browser that starts a walk gets a private cookie, and only that browser sees the walk on its shelf and can change it. A journal's link can be shared: anyone with it can read the journal, but raw audio is only ever served to the browser that recorded it. The sample is read-only for everyone.

Raw audio goes to ElevenLabs for transcription; transcripts and photos go to Google's Gemini API. Exports never include raw audio, and the route is trimmed by 300 m at each end unless you turn that off.

To share the site itself without letting strangers spend your API quota, set `ACCESS_CODE`: starting a walk or an import then asks for it. Quota guards also apply: `LLM_MAX_PER_DAY` caps model calls per day and `SUBMIT_LIMIT` caps journal runs per visitor per 10 minutes.

## Models and services

| Model / service | Use | Licence / terms |
| --- | --- | --- |
| Gemma 4 (`gemma-4-26b-a4b-it` by default; `LLM_MODEL` or `walkworth.config.toml`) via the Gemini API | Extraction, photo captions, translation, journal writing | Gemma Terms of Use; Gemini API Terms |
| ElevenLabs Scribe (`scribe_v1` on the deployed site; set with `ELEVENLABS_STT_MODEL`) | Speech to text with word timings and confidence | ElevenLabs Terms |
| Temporal | Pipeline orchestration | MIT |
| Render | Hosting | Render Terms |

## Sample data and credits

`fixtures/sinhagad-sample/` is **sample data**, marked as such everywhere it appears. The transcripts are fiction, and until replaced the voice notes are a computer voice reading them. The GPX follows the real Sinhagad Trail from OpenStreetMap, but its times and elevations are invented. The photos are by other people, used under open licences and credited on every print:

- Suraj Digrase, CC0, via Wikimedia Commons (5 photos)
- Tanaya Badgandi, CC BY-SA 4.0, via Wikimedia Commons (sunrise)

Map data © OpenStreetMap contributors, ODbL. Fonts: Familjen Grotesk, Source Serif 4 and Tiro Devanagari Marathi (SIL Open Font License), self-hosted via Fontsource. Icons: Phosphor (MIT).

Built by Aditya Kumar · [GitHub](https://github.com/ADITYA-KUMAR-2358)
