import { useState, useRef, useEffect } from 'react';
import {
  parseOpmlFile,
  subscribeOpmlFeeds,
  getOpmlExportUrl,
  type OpmlFeedItem,
} from '../-podcasts.api';
import styles from './podcasts-page.module.css';

interface PodcastOpmlModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSubscriptionsImported?: (count: number) => void;
}

export function PodcastOpmlModal({
  isOpen,
  onClose,
  onSubscriptionsImported,
}: PodcastOpmlModalProps) {
  const [activeTab, setActiveTab] = useState<'import' | 'export'>('import');
  const [isParsing, setIsParsing] = useState(false);
  const [isSubscribing, setIsSubscribing] = useState(false);
  const [parsedFeeds, setParsedFeeds] = useState<OpmlFeedItem[]>([]);
  const [selectedUrls, setSelectedUrls] = useState<Set<string>>(new Set());
  const [errorMessage, setErrorMessage] = useState('');
  const [successMessage, setSuccessMessage] = useState('');
  const [isDragging, setIsDragging] = useState(false);

  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (isOpen) {
      setActiveTab('import');
      setIsParsing(false);
      setIsSubscribing(false);
      setParsedFeeds([]);
      setSelectedUrls(new Set());
      setErrorMessage('');
      setSuccessMessage('');
    }
  }, [isOpen]);

  if (!isOpen) return null;

  const handleFileSelect = async (file: File) => {
    if (!file) return;
    setErrorMessage('');
    setSuccessMessage('');
    setIsParsing(true);

    try {
      const res = await parseOpmlFile(file);
      if (!res.success || !res.feeds.length) {
        setErrorMessage(res.error || 'No podcast subscriptions found in this OPML file.');
        setParsedFeeds([]);
        setSelectedUrls(new Set());
      } else {
        setParsedFeeds(res.feeds);
        setSelectedUrls(new Set(res.feeds.map((f) => f.feed_url)));
      }
    } catch (err: any) {
      setErrorMessage(err?.message || 'Failed to parse file.');
    } finally {
      setIsParsing(false);
    }
  };

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      const file = e.dataTransfer.files[0];
      handleFileSelect(file);
    }
  };

  const toggleFeedSelection = (url: string) => {
    const next = new Set(selectedUrls);
    if (next.has(url)) {
      next.delete(url);
    } else {
      next.add(url);
    }
    setSelectedUrls(next);
  };

  const handleSelectAll = () => {
    setSelectedUrls(new Set(parsedFeeds.map((f) => f.feed_url)));
  };

  const handleDeselectAll = () => {
    setSelectedUrls(new Set());
  };

  const handleImportSelected = async () => {
    const selected = parsedFeeds.filter((f) => selectedUrls.has(f.feed_url));
    if (!selected.length) return;

    setIsSubscribing(true);
    setErrorMessage('');
    setSuccessMessage('');

    try {
      const res = await subscribeOpmlFeeds(
        selected.map((s) => ({ title: s.title, feed_url: s.feed_url })),
      );
      if (res.success) {
        setSuccessMessage(`Successfully imported ${res.imported_count} podcast subscriptions!`);
        if (onSubscriptionsImported) {
          onSubscriptionsImported(res.imported_count);
        }
      } else {
        setErrorMessage(res.error || 'Failed to subscribe to feeds.');
      }
    } catch (err: any) {
      setErrorMessage(err?.message || 'Failed to import feeds.');
    } finally {
      setIsSubscribing(false);
    }
  };

  return (
    <div className={styles.modalBackdrop} onClick={onClose}>
      <div
        className={styles.modalDialog}
        style={{ maxWidth: 640 }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className={styles.modalHeader}>
          <div className={styles.modalTitleGroup}>
            <div className={styles.modalIcon}>
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
                <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
              </svg>
            </div>
            <div>
              <h2 className={styles.modalTitle}>Podcast Subscriptions (OPML)</h2>
              <p className={styles.modalSubtitle}>
                Migrate subscriptions between SoulSync, Apple Podcasts, Pocket Casts, and Overcast.
              </p>
            </div>
          </div>
          <button
            type="button"
            className={styles.modalCloseButton}
            onClick={onClose}
            aria-label="Close"
          >
            &times;
          </button>
        </div>

        {/* Modal Tab Switcher */}
        <div className={styles.modalTabRow}>
          <button
            type="button"
            className={`${styles.modalTabBtn} ${activeTab === 'import' ? styles.modalTabActive : ''}`}
            onClick={() => setActiveTab('import')}
          >
            Import OPML
          </button>
          <button
            type="button"
            className={`${styles.modalTabBtn} ${activeTab === 'export' ? styles.modalTabActive : ''}`}
            onClick={() => setActiveTab('export')}
          >
            Export OPML
          </button>
        </div>

        <div className={styles.modalBody}>
          {activeTab === 'import' && (
            <>
              {parsedFeeds.length === 0 ? (
                <div
                  className={`${styles.opmlDropzone} ${isDragging ? styles.opmlDropzoneActive : ''}`}
                  onDragOver={(e) => {
                    e.preventDefault();
                    setIsDragging(true);
                  }}
                  onDragLeave={() => setIsDragging(false)}
                  onDrop={onDrop}
                  onClick={() => fileInputRef.current?.click()}
                >
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept=".opml,.xml"
                    style={{ display: 'none' }}
                    onChange={(e) => {
                      if (e.target.files && e.target.files.length > 0) {
                        handleFileSelect(e.target.files[0]);
                      }
                    }}
                  />
                  <div className={styles.opmlDropzoneIcon}>
                    {isParsing ? (
                      <div className={styles.spinner} style={{ width: 28, height: 28, borderWidth: 3 }} />
                    ) : (
                      <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                        <polyline points="17 8 12 3 7 8" />
                        <line x1="12" y1="3" x2="12" y2="15" />
                      </svg>
                    )}
                  </div>
                  <h3 className={styles.opmlDropzoneTitle}>
                    {isParsing ? 'Parsing OPML File…' : 'Drop an .opml or .xml file here'}
                  </h3>
                  <p className={styles.opmlDropzoneText}>
                    or click to browse from your computer. Exported from Pocket Casts, Apple Podcasts, Overcast, AntennaPod, etc.
                  </p>
                </div>
              ) : (
                <div className={styles.opmlPreviewContainer}>
                  <div className={styles.opmlPreviewHeader}>
                    <div>
                      <span className={styles.opmlFoundCount}>
                        Found <strong>{parsedFeeds.length}</strong> podcasts in file
                      </span>
                      <span className={styles.opmlSelectedCount}>
                        ({selectedUrls.size} selected)
                      </span>
                    </div>
                    <div className={styles.opmlSelectButtons}>
                      <button
                        type="button"
                        className={styles.opmlTextBtn}
                        onClick={handleSelectAll}
                      >
                        Select All
                      </button>
                      <span className={styles.opmlBtnSep}>·</span>
                      <button
                        type="button"
                        className={styles.opmlTextBtn}
                        onClick={handleDeselectAll}
                      >
                        Deselect All
                      </button>
                    </div>
                  </div>

                  <div className={styles.opmlFeedList}>
                    {parsedFeeds.map((feed) => {
                      const isSelected = selectedUrls.has(feed.feed_url);
                      return (
                        <div
                          key={feed.feed_url}
                          className={`${styles.opmlFeedRow} ${isSelected ? styles.opmlFeedRowSelected : ''}`}
                          onClick={() => toggleFeedSelection(feed.feed_url)}
                        >
                          <input
                            type="checkbox"
                            checked={isSelected}
                            onChange={() => toggleFeedSelection(feed.feed_url)}
                            onClick={(e) => e.stopPropagation()}
                            className={styles.opmlCheckbox}
                          />
                          <div className={styles.opmlFeedInfo}>
                            <h4 className={styles.opmlFeedTitle}>{feed.title}</h4>
                            <span className={styles.opmlFeedUrl}>{feed.feed_url}</span>
                            {feed.description && (
                              <p className={styles.opmlFeedDesc}>{feed.description}</p>
                            )}
                          </div>
                        </div>
                      );
                    })}
                  </div>

                  <div className={styles.opmlPreviewActions}>
                    <button
                      type="button"
                      className={styles.cancelButton}
                      onClick={() => {
                        setParsedFeeds([]);
                        setSelectedUrls(new Set());
                      }}
                      disabled={isSubscribing}
                    >
                      Choose Another File
                    </button>
                    <button
                      type="button"
                      className={styles.confirmButton}
                      disabled={isSubscribing || selectedUrls.size === 0}
                      onClick={handleImportSelected}
                    >
                      {isSubscribing
                        ? 'Importing…'
                        : `Import ${selectedUrls.size} to Watchlist`}
                    </button>
                  </div>
                </div>
              )}

              {errorMessage && <div className={styles.opmlErrorBanner}>{errorMessage}</div>}
              {successMessage && <div className={styles.opmlSuccessBanner}>{successMessage}</div>}
            </>
          )}

          {activeTab === 'export' && (
            <div className={styles.opmlExportContainer}>
              <div className={styles.opmlExportIcon}>
                <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                  <polyline points="7 10 12 15 17 10" />
                  <line x1="12" y1="15" x2="12" y2="3" />
                </svg>
              </div>
              <h3 className={styles.opmlExportTitle}>Export Subscribed Podcasts</h3>
              <p className={styles.opmlExportText}>
                Download a standard OPML 2.0 XML file containing all your SoulSync podcast subscriptions.
                You can import this file into Apple Podcasts, Pocket Casts, Overcast, or any other player.
              </p>
              <a
                href={getOpmlExportUrl()}
                download="soulsync-podcasts.opml"
                className={styles.opmlDownloadBtn}
              >
                Download soulsync-podcasts.opml
              </a>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
