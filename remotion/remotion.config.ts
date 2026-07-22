import {Config} from '@remotion/cli/config';

// Allow absolute media paths passed from the Python pipeline (task materials,
// narration, BGM, fonts) so OffthreadVideo / Audio can read them during render.
Config.setChromiumOpenGlRenderer('angle');
Config.setVideoImageFormat('jpeg');
Config.setOverwriteOutput(true);
