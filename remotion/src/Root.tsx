import React from 'react';
import {Composition} from 'remotion';
import {MoneyPrinterVideo} from './MoneyPrinterVideo';
import {defaultProps as fallbackProps, type MoneyPrinterProps} from './types';
import inputPropsJson from '../input-props.json';

// Per-task projects stage real data into input-props.json. Importing it as
// defaultProps makes Studio show the video without requiring --props (which
// also locks the sidebar against visual edits).
const studioDefaultProps = {
  ...fallbackProps,
  ...(inputPropsJson as MoneyPrinterProps),
} satisfies MoneyPrinterProps;

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="MoneyPrinterVideo"
        component={MoneyPrinterVideo}
        durationInFrames={studioDefaultProps.durationInFrames}
        fps={studioDefaultProps.fps}
        width={studioDefaultProps.width}
        height={studioDefaultProps.height}
        defaultProps={studioDefaultProps}
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
