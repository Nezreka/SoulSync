import { describe, expect, it } from 'vitest';

import type { ImportAutoImportResult } from './-import.types';

import {
  filterAutoImportResults,
  getAutoImportCounts,
  getAutoImportStatusMeta,
} from './-import.helpers';

const partial: ImportAutoImportResult = {
  id: 1,
  status: 'partial',
  folder_name: 'Album',
};

describe('partial auto-import results', () => {
  it('keeps partial batches distinct from completed imports', () => {
    expect(getAutoImportCounts([partial])).toEqual({ imported: 0, review: 0, failed: 1 });
    expect(filterAutoImportResults([partial], 'failed')).toEqual([partial]);
    expect(filterAutoImportResults([partial], 'imported')).toEqual([]);
    expect(getAutoImportStatusMeta('partial')).toMatchObject({
      label: 'Partially Imported',
      className: 'failed',
    });
  });
});
