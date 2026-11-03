import React, { useRef } from 'react';
import PixelOverlay from './PixelOverlay';

// Covers the nearest positioned parent with a pixel dissolve each time
// `triggerKey` changes - not on first load. `preset` picks the look at the
// moment of the change, e.g. a bigger sweep when a chat has just been created.
function PixelTransition({ triggerKey, preset = 'switch' }) {
  const firstKey = useRef(triggerKey);
  if (triggerKey === firstKey.current) return null;
  return <PixelOverlay key={triggerKey} preset={preset} />;
}

export default PixelTransition;
