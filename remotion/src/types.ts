export type SlideSide = 'left' | 'right' | 'top' | 'bottom';

export type TransitionName =
  | 'none'
  | 'FadeIn'
  | 'FadeOut'
  | 'SlideIn'
  | 'SlideOut'
  | 'ZoomIn'
  | 'ZoomOut';

export type ClipProps = {
  src: string;
  startFromSeconds: number;
  durationInFrames: number;
  speed: number;
  transition: TransitionName | string;
  slideSide: SlideSide | string;
};

export type SubtitleCue = {
  text: string;
  startFrame: number;
  durationInFrames: number;
};

export type SubtitleStyle = {
  enabled: boolean;
  cues: SubtitleCue[];
  fontPath: string;
  fontSize: number;
  color: string;
  strokeColor: string;
  strokeWidth: number;
  position: string;
  customPosition: number;
  backgroundColor: string | null;
  roundedBackground: boolean;
};

export type MoneyPrinterProps = {
  clips: ClipProps[];
  width: number;
  height: number;
  fps: number;
  durationInFrames: number;
  visualOnly: boolean;
  narrationSrc: string;
  voiceVolume: number;
  bgmSrc: string;
  bgmVolume: number;
  transitionDurationInFrames: number;
  subtitles: SubtitleStyle;
};

export const defaultProps: MoneyPrinterProps = {
  clips: [],
  width: 1080,
  height: 1920,
  fps: 30,
  durationInFrames: 30,
  visualOnly: false,
  narrationSrc: '',
  voiceVolume: 1,
  bgmSrc: '',
  bgmVolume: 0.2,
  transitionDurationInFrames: 30,
  subtitles: {
    enabled: false,
    cues: [],
    fontPath: '',
    fontSize: 60,
    color: '#FFFFFF',
    strokeColor: '#000000',
    strokeWidth: 1.5,
    position: 'bottom',
    customPosition: 70,
    backgroundColor: null,
    roundedBackground: false,
  },
};
