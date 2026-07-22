# MoneyPrinterTurbo Remotion renderer

Optional full-composition renderer used when `video_renderer = "remotion"` in
`config.toml`.

This directory is the **shared template**. Each generated video also gets a
standalone editable project under:

```text
storage/tasks/<task_id>/remotion-<title-slug>-<task8>-<index>/
```

That project includes composition source, staged media in `public/`, and
`input-props.json`. The folder and `package.json` name include the task title
slug plus a short task id so Studio projects stay unique and readable.

## Setup (once)

1. Install [Node.js 18+](https://nodejs.org/)
2. From this directory:

```bash
npm install
```

3. Set `video_renderer = "remotion"` in `config.toml`, or choose Remotion in the WebUI.

## Open an existing project (in place)

WebUI: **Open Remotion Project** panel — pick a past `remotion-*` folder, add
optional follow-up prompts, then **Apply follow-ups & re-render**. The same
project folder is updated (no fork). Whether materials/BGM change is inferred
from the prompts.

CLI:

```bash
uv run python cli.py \
  --remotion-project storage/tasks/<id>/remotion-<slug>-<task8>-1 \
  --remotion-followup "Make the tone more urgent" \
  --remotion-followup "Use more product close-up footage"
```

Empty follow-ups re-render the current project (including Studio edits) as-is.

## Edit a generated video

```bash
cd storage/tasks/<task_id>/remotion-<slug>-<task8>-1
npx remotion studio
```

`input-props.json` is imported as Studio default props. Re-render:

```bash
npx remotion render src/index.ts MoneyPrinterVideo out.mp4 --props=input-props.json --overwrite
```

`node_modules` inside each task project is a symlink back to this template's
`node_modules`.

## License

Remotion requires a company license in some cases. See
https://www.remotion.dev/docs/license
