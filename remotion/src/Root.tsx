import React from 'react';
import {Composition} from 'remotion';
import {MoneyPrinterVideo} from './MoneyPrinterVideo';
import {defaultProps, type MoneyPrinterProps} from './types';

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="MoneyPrinterVideo"
        component={MoneyPrinterVideo}
        durationInFrames={defaultProps.durationInFrames}
        fps={defaultProps.fps}
        width={defaultProps.width}
        height={defaultProps.height}
        defaultProps={defaultProps}
        calculateMetadata={async ({props}: {props: MoneyPrinterProps}) => {
          const durationInFrames = Math.max(
            1,
            props.durationInFrames ||
              props.clips.reduce(
                (total, clip) => total + Math.max(1, clip.durationInFrames),
                0,
              ),
          );
          return {
            durationInFrames,
            fps: props.fps || 30,
            width: props.width || 1080,
            height: props.height || 1920,
            props,
          };
        }}
      />
    </>
  );
};
