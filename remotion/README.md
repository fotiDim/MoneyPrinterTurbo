# MoneyPrinterTurbo Remotion renderer

Optional full-composition renderer used when `video_renderer = "remotion"` in
`config.toml`. Python builds a props JSON and runs:

```bash
npx remotion render src/index.ts MoneyPrinterVideo <output.mp4> --props=<props.json>
```

## Setup

1. Install [Node.js 18+](https://nodejs.org/)
2. From this directory:

```bash
npm install
```

3. Set `video_renderer = "remotion"` in `config.toml`, or choose Remotion in the WebUI.

## License

Remotion requires a company license in some cases. See
https://www.remotion.dev/docs/license
