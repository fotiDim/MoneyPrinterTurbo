# MoneyPrinterTurbo Remotion renderer

Optional full-composition renderer used when `video_renderer = "remotion"` in
`config.toml`.

This directory is the **shared template**. Each generated video also gets a
standalone editable project under:

```text
storage/tasks/<task_id>/remotion-<index>/
```

That project includes composition source, staged media in `public/`, and
`input-props.json`. MoneyPrinterTurbo renders from that project so Studio edits
match re-renders.

## Setup (once)

1. Install [Node.js 18+](https://nodejs.org/)
2. From this directory:

```bash
npm install
```

3. Set `video_renderer = "remotion"` in `config.toml`, or choose Remotion in the WebUI.

## Edit a generated video

```bash
cd storage/tasks/<task_id>/remotion-1
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
