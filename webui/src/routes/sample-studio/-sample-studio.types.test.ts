import { describe, expect, it } from 'vitest';

import {
  analysisErrorMessage,
  DEFAULT_FILTERS,
  isAnalysisError,
  STASH_FORMAT_LABEL,
  STEM_LABEL,
  STEM_NAMES,
} from './-sample-studio.types';

describe('analysis status helpers', () => {
  it('isAnalysisError detects the error: prefix only', () => {
    expect(isAnalysisError('error: disk full')).toBe(true);
    expect(isAnalysisError('error')).toBe(true); // prefix match — statuses are `error: …`
    expect(isAnalysisError('done')).toBe(false);
    expect(isAnalysisError('queued')).toBe(false);
    expect(isAnalysisError('analyzing')).toBe(false);
    expect(isAnalysisError('idle')).toBe(false);
  });

  it('analysisErrorMessage returns the human tail', () => {
    expect(analysisErrorMessage('error: disk full')).toBe('disk full');
    expect(analysisErrorMessage('error:')).toBe('Analysis failed');
    expect(analysisErrorMessage('error:   ')).toBe('Analysis failed');
  });
});

describe('type constants', () => {
  it('DEFAULT_FILTERS is all/all/all', () => {
    expect(DEFAULT_FILTERS).toEqual({ quality: 'all', tempo: 'all', length: 'all' });
  });

  it('STEM_NAMES matches the backend STEMS order', () => {
    expect(STEM_NAMES).toEqual(['drums', 'vocals', 'bass', 'other']);
    expect(Object.keys(STEM_LABEL)).toEqual(['drums', 'vocals', 'bass', 'other']);
  });

  it('STASH_FORMAT_LABEL covers every format', () => {
    expect(STASH_FORMAT_LABEL).toEqual({
      wav16: 'WAV 16-bit',
      wav24: 'WAV 24-bit',
      flac: 'FLAC 24-bit',
    });
  });
});
