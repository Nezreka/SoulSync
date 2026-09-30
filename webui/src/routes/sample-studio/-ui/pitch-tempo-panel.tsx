import styles from './sample-studio-page.module.css';

interface PitchTempoPanelProps {
  sourceBpm: number | null;
  pitchSt: number;
  targetBpm: number | null;
  onParamsChange: (pitchSt: number, targetBpm: number | null) => void;
  rendering: boolean;
  previewEngine: string | null;
  mode: 'original' | 'preview';
  onModeChange: (mode: 'original' | 'preview') => void;
  disabled: boolean;
}

/**
 * Pitch (±12 semitones) + target-BPM controls. The parent debounces changes
 * and POSTs /api/sample/preview; this panel is the controls + the
 * original/processed toggle. Time-stretch needs a known source BPM.
 */
export function PitchTempoPanel({
  sourceBpm,
  pitchSt,
  targetBpm,
  onParamsChange,
  rendering,
  previewEngine,
  mode,
  onModeChange,
  disabled,
}: PitchTempoPanelProps) {
  const isNeutral = Math.abs(pitchSt) < 0.01 && (targetBpm == null || targetBpm === sourceBpm);

  return (
    <div className={styles.pitchPanel}>
      <div className={styles.chopTrayHeader}>
        <span>Pitch &amp; Tempo</span>
        <span className={styles.chopActions}>
          <button
            type="button"
            className={styles.transportBtn}
            data-on={mode === 'original'}
            onClick={() => onModeChange('original')}
            disabled={disabled}
          >
            Original
          </button>
          <button
            type="button"
            className={styles.transportBtn}
            data-on={mode === 'preview'}
            onClick={() => onModeChange('preview')}
            disabled={disabled || isNeutral}
            title={isNeutral ? 'Move pitch or tempo first' : 'Hear the processed version'}
          >
            Processed
          </button>
        </span>
      </div>

      <div className={styles.pitchRow}>
        <label className={styles.pitchLabel}>
          Pitch
          <input
            type="range"
            min={-12}
            max={12}
            step={0.5}
            value={pitchSt}
            disabled={disabled}
            onChange={(e) => onParamsChange(Number(e.target.value), targetBpm)}
            className={styles.sliderCtl}
          />
        </label>
        <span className={styles.pitchValue}>{pitchSt > 0 ? `+${pitchSt}` : `${pitchSt}`} st</span>
        <button
          type="button"
          className={styles.transportBtn}
          disabled={disabled || pitchSt === 0}
          onClick={() => onParamsChange(0, targetBpm)}
          title="Reset pitch"
        >
          Reset
        </button>
      </div>

      <div className={styles.pitchRow}>
        <label className={styles.pitchLabel}>
          Target BPM
          <input
            type="number"
            min={20}
            max={300}
            step={1}
            value={targetBpm ?? ''}
            placeholder={sourceBpm ? String(Math.round(sourceBpm)) : '—'}
            disabled={disabled || !sourceBpm}
            onChange={(e) => {
              const v = e.target.value === '' ? null : Number(e.target.value);
              onParamsChange(pitchSt, v);
            }}
            className={styles.bpmInput}
          />
        </label>
        <span className={styles.pitchValue}>
          {sourceBpm ? `from ${sourceBpm.toFixed(1)}` : 'BPM unknown — analyze first'}
        </span>
        <button
          type="button"
          className={styles.transportBtn}
          disabled={disabled || !sourceBpm || targetBpm === sourceBpm}
          onClick={() => onParamsChange(pitchSt, sourceBpm)}
          title="Reset tempo"
        >
          Reset
        </button>
      </div>

      <div className={styles.statusLine}>
        {rendering
          ? 'Rendering preview…'
          : previewEngine
            ? `Preview rendered (${previewEngine === 'rubberband' ? 'Rubber Band' : 'fast preview engine'}) — final saves use the quality engine`
            : isNeutral
              ? 'Move pitch or tempo to hear a processed preview'
              : 'Processed preview ready — toggle above to compare'}
      </div>
    </div>
  );
}
