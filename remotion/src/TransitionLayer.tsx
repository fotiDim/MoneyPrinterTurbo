import React from 'react';
import {interpolate, useCurrentFrame} from 'remotion';
import type {SlideSide, TransitionName} from './types';

type TransitionLayerProps = {
  children: React.ReactNode;
  transition: TransitionName | string;
  slideSide: SlideSide | string;
  durationInFrames: number;
  transitionDurationInFrames: number;
};

const clampProgress = (value: number) => Math.min(1, Math.max(0, value));

export const TransitionLayer: React.FC<TransitionLayerProps> = ({
  children,
  transition,
  slideSide,
  durationInFrames,
  transitionDurationInFrames,
}) => {
  const frame = useCurrentFrame();
  const t = Math.max(1, transitionDurationInFrames);
  const name = transition || 'none';

  let opacity = 1;
  let translateX = 0;
  let translateY = 0;
  let scale = 1;

  if (name === 'FadeIn') {
    opacity = clampProgress(
      interpolate(frame, [0, t], [0, 1], {
        extrapolateLeft: 'clamp',
        extrapolateRight: 'clamp',
      }),
    );
  } else if (name === 'FadeOut') {
    opacity = clampProgress(
      interpolate(frame, [durationInFrames - t, durationInFrames], [1, 0], {
        extrapolateLeft: 'clamp',
        extrapolateRight: 'clamp',
      }),
    );
  } else if (name === 'SlideIn') {
    const progress = clampProgress(
      interpolate(frame, [0, t], [0, 1], {
        extrapolateLeft: 'clamp',
        extrapolateRight: 'clamp',
      }),
    );
    if (slideSide === 'left') translateX = -100 + 100 * progress;
    else if (slideSide === 'right') translateX = 100 - 100 * progress;
    else if (slideSide === 'top') translateY = -100 + 100 * progress;
    else translateY = 100 - 100 * progress;
  } else if (name === 'SlideOut') {
    const progress = clampProgress(
      interpolate(frame, [durationInFrames - t, durationInFrames], [0, 1], {
        extrapolateLeft: 'clamp',
        extrapolateRight: 'clamp',
      }),
    );
    if (slideSide === 'left') translateX = -100 * progress;
    else if (slideSide === 'right') translateX = 100 * progress;
    else if (slideSide === 'top') translateY = -100 * progress;
    else translateY = 100 * progress;
  } else if (name === 'ZoomIn') {
    scale = interpolate(frame, [0, t], [1, 1.2], {
      extrapolateLeft: 'clamp',
      extrapolateRight: 'clamp',
    });
  } else if (name === 'ZoomOut') {
    scale = interpolate(frame, [0, t], [1.2, 1], {
      extrapolateLeft: 'clamp',
      extrapolateRight: 'clamp',
    });
  }

  return (
    <div
      style={{
        width: '100%',
        height: '100%',
        opacity,
        transform: `translate(${translateX}%, ${translateY}%) scale(${scale})`,
        transformOrigin: 'center center',
      }}
    >
      {children}
    </div>
  );
};
