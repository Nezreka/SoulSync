import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { QrImage, qrPath } from './qr-image';
import { Ecc, QrCode } from './qrcodegen';

describe('the pairing QR', () => {
  it('draws every dark module, with a quiet zone', () => {
    const text = '{"v":1,"code":"ABCD-EFGH-JKMN-PQRS"}';
    const qr = QrCode.encodeText(text, Ecc.MEDIUM);
    const { size, d } = qrPath(text);
    expect(size).toBe(qr.size + 8);
    let dark = 0;
    for (let y = 0; y < qr.size; y++)
      for (let x = 0; x < qr.size; x++) if (qr.getModule(x, y)) dark++;
    expect(d.split('M').length - 1).toBe(dark);
    // the finder pattern's corner sits just inside the quiet zone
    expect(d.startsWith('M4,4h1v1h-1z')).toBe(true);
  });

  it('is a version 1 code for a short text and grows with it', () => {
    expect(QrCode.encodeText('HELLO', Ecc.MEDIUM).version).toBe(1);
    expect(QrCode.encodeText('x'.repeat(300), Ecc.MEDIUM).version).toBeGreaterThan(5);
  });

  it('renders an image with its label', () => {
    const { getByRole } = render(<QrImage text="hello" label="Pairing code" />);
    expect(getByRole('img').getAttribute('aria-label')).toBe('Pairing code');
  });
});
