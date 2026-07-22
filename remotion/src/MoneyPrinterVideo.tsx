import React from 'react';
import {
  AbsoluteFill,
  Audio,
  OffthreadVideo,
  Sequence,
  useVideoConfig,
} from 'remotion';
import {SubtitlesOverlay} from './SubtitlesOverlay';
import {TransitionLayer} from './TransitionLayer';
import type {MoneyPrinterProps} from './types';

const toMediaSrc = (filePath: string) => {
  if (!filePath) {
    return '';
  }
  if (
    filePath.startsWith('http://') ||
    filePath.startsWith('https://') ||
    filePath.startsWith('file://') ||
    filePath.startsWith('data:')
  ) {
    return filePath;
  }
  // Remotion's compositor can read absolute filesystem paths during render.
  return filePath;
};

export const MoneyPrinterVideo: React.FC<MoneyPrinterProps> = (props) => {
  const {fps} = useVideoConfig();
  let from = 0;

  return (
    <AbsoluteFill style={{backgroundColor: '#000'}}>
      {props.clips.map((clip, index) => {
        const durationInFrames = Math.max(1, clip.durationInFrames);
        const startFrom = Math.max(
          0,
          Math.round(clip.startFromSeconds * fps),
        );
        const speed = clip.speed > 0 ? clip.speed : 1;
        const sequenceFrom = from;
        from += durationInFrames;

        return (
          <Sequence
            key={`clip-${index}-${sequenceFrom}`}
            from={sequenceFrom}
            durationInFrames={durationInFrames}
          >
            <AbsoluteFill style={{backgroundColor: '#000'}}>
              <TransitionLayer
                transition={clip.transition}
                slideSide={clip.slideSide}
                durationInFrames={durationInFrames}
                transitionDurationInFrames={props.transitionDurationInFrames}
              >
                <AbsoluteFill>
                  <OffthreadVideo
                    src={toMediaSrc(clip.src)}
                    startFrom={startFrom}
                    playbackRate={speed}
                    muted
                    style={{
                      width: '100%',
                      height: '100%',
                      objectFit: 'contain',
                    }}
                  />
                </AbsoluteFill>
              </TransitionLayer>
            </AbsoluteFill>
          </Sequence>
        );
      })}

      {!props.visualOnly && props.narrationSrc ? (
        <Audio
          src={toMediaSrc(props.narrationSrc)}
          volume={Math.max(0, props.voiceVolume)}
        />
      ) : null}

      {!props.visualOnly && props.bgmSrc && props.bgmVolume > 0 ? (
        <Audio
          src={toMediaSrc(props.bgmSrc)}
          volume={Math.max(0, props.bgmVolume)}
        />
      ) : null}

      {!props.visualOnly ? (
        <SubtitlesOverlay subtitles={props.subtitles} />
      ) : null}
    </AbsoluteFill>
  );
};
