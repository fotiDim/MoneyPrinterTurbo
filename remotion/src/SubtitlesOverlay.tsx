import React, {useEffect, useState} from 'react';
import {
  AbsoluteFill,
  continueRender,
  delayRender,
  Sequence,
} from 'remotion';
import type {SubtitleStyle} from './types';

type SubtitlesOverlayProps = {
  subtitles: SubtitleStyle;
};

const toFileUrl = (filePath: string) => {
  if (!filePath) {
    return '';
  }
  if (filePath.startsWith('file://')) {
    return filePath;
  }
  const normalized = filePath.replace(/\\/g, '/');
  if (/^[A-Za-z]:\//.test(normalized)) {
    return `file:///${normalized}`;
  }
  return `file://${normalized}`;
};

export const SubtitlesOverlay: React.FC<SubtitlesOverlayProps> = ({
  subtitles,
}) => {
  const enabled = Boolean(subtitles.enabled && subtitles.cues.length);
  const [fontFamily, setFontFamily] = useState('sans-serif');
  const [handle] = useState(() =>
    enabled ? delayRender('Loading Remotion subtitle font') : null,
  );

  useEffect(() => {
    if (!enabled || handle === null) {
      return;
    }
    let cancelled = false;
    const load = async () => {
      try {
        if (subtitles.fontPath) {
          const family = 'MoneyPrinterSubtitleFont';
          const face = new FontFace(
            family,
            `url(${toFileUrl(subtitles.fontPath)})`,
          );
          await face.load();
          document.fonts.add(face);
          if (!cancelled) {
            setFontFamily(family);
          }
        }
      } catch (error) {
        console.warn('Failed to load subtitle font', error);
      } finally {
        continueRender(handle);
      }
    };
    load();
    return () => {
      cancelled = true;
    };
  }, [enabled, handle, subtitles.fontPath]);

  if (!enabled) {
    return null;
  }

  const positionStyle = (): React.CSSProperties => {
    const base: React.CSSProperties = {
      position: 'absolute',
      left: '5%',
      width: '90%',
      display: 'flex',
      justifyContent: 'center',
      textAlign: 'center',
      boxSizing: 'border-box',
    };
    if (subtitles.position === 'top') {
      return {...base, top: '8%'};
    }
    if (subtitles.position === 'center') {
      return {...base, top: '50%', transform: 'translateY(-50%)'};
    }
    if (subtitles.position === 'custom') {
      return {...base, top: `${subtitles.customPosition}%`};
    }
    return {...base, bottom: '10%'};
  };

  return (
    <AbsoluteFill style={{pointerEvents: 'none'}}>
      {subtitles.cues.map((cue, index) => (
        <Sequence
          key={`cue-${index}-${cue.startFrame}`}
          from={cue.startFrame}
          durationInFrames={cue.durationInFrames}
          layout="none"
        >
          <div style={positionStyle()}>
            <span
              style={{
                fontFamily,
                fontSize: subtitles.fontSize,
                color: subtitles.color,
                WebkitTextStroke: `${Math.max(
                  0,
                  subtitles.strokeWidth,
                )}px ${subtitles.strokeColor}`,
                paintOrder: 'stroke fill',
                lineHeight: 1.25,
                whiteSpace: 'pre-wrap',
                maxWidth: '100%',
                padding: subtitles.backgroundColor
                  ? `${Math.round(subtitles.fontSize * 0.25)}px ${Math.round(
                      subtitles.fontSize * 0.45,
                    )}px`
                  : 0,
                backgroundColor: subtitles.backgroundColor || 'transparent',
                borderRadius: subtitles.roundedBackground
                  ? Math.round(subtitles.fontSize * 0.35)
                  : 0,
              }}
            >
              {cue.text}
            </span>
          </div>
        </Sequence>
      ))}
    </AbsoluteFill>
  );
};
