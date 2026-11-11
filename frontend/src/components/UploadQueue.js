import React, { useRef } from 'react';
import { formatBytes } from '../uploader';
import { PROCESSING, INTERRUPTED, isTransferring, isWorking } from '../uploadStatus';

function UploadQueue({ uploadQueue, unfinished = [], onResume, onCancel, onPickFiles, onDiscardUnfinished }) {
  const pickerRef = useRef(null);
  if (uploadQueue.length === 0 && unfinished.length === 0) return null;

  return (
    <div className="upload-queue">
      {uploadQueue.length > 0 && <div className="queue-header">uploads ({uploadQueue.length})</div>}
      {uploadQueue.map((upload) => {
        const interrupted = upload.status === INTERRUPTED;
        const transferring = isTransferring(upload.status);
        const active = isWorking(upload.status);
        const canCancel = transferring && onCancel;
        return (
        <div key={upload.uploadId} className="upload-progress">
          <div className="progress-info">
            <span className="progress-message">
              {active && <span className="progress-live-dot" />}
              {upload.message}
            </span>
            <span className="progress-percentage">{upload.progress}%</span>
          </div>
          <div
            className="progress-bar-container"
            role="progressbar"
            aria-valuenow={upload.progress}
            aria-valuemin={0}
            aria-valuemax={100}
          >
            <div
              className={`progress-bar-fill ${active && !transferring ? 'active' : ''} ${interrupted ? 'paused' : ''}`}
              style={{ width: `${upload.progress}%` }}
            />
          </div>

          {transferring && upload.bytesTotal > 0 && (
            <div className="progress-file">
              {upload.fileCount > 1 ? `file ${upload.fileIndex + 1} of ${upload.fileCount} · ` : ''}
              {upload.fileName} · {formatBytes(upload.bytesLoaded)} of {formatBytes(upload.bytesTotal)}
            </div>
          )}
          {interrupted && upload.error && <div className="progress-file">{upload.error}</div>}

          {!transferring && upload.currentFile && (
            <div className="progress-file">processing: {upload.currentFile}</div>
          )}
          {!transferring && upload.totalFiles > 0 && PROCESSING.has(upload.status) && (
            <div className="progress-count">
              {upload.processedFiles} / {upload.totalFiles} files
              {upload.bytesTotal > 0 && ` · uploaded ${formatBytes(upload.bytesTotal)}`}
            </div>
          )}
          {!transferring && upload.totalFiles > 0 && !PROCESSING.has(upload.status) && (
            <div className="progress-count">{upload.processedFiles} / {upload.totalFiles} files</div>
          )}

          {(interrupted || canCancel) && (
            <div className="upload-actions">
              {interrupted && onResume && (
                <button type="button" className="upload-action" onClick={() => onResume(upload.uploadId)}>
                  resume
                </button>
              )}
              {canCancel && (
                <button type="button" className="upload-action" onClick={() => onCancel(upload.uploadId)}>
                  cancel
                </button>
              )}
            </div>
          )}
        </div>
        );
      })}

      {unfinished.length > 0 && (
        <div className="upload-unfinished">
          <div className="queue-header">unfinished uploads ({unfinished.length})</div>
          {unfinished.map((item) => (
            <div key={item.upload_id} className="upload-progress">
              <div className="progress-info">
                <span className="progress-message">{item.filename}</span>
                <span className="progress-percentage">{Math.floor((item.received / item.size) * 100)}%</span>
              </div>
              <div className="progress-bar-container">
                <div
                  className="progress-bar-fill paused"
                  style={{ width: `${Math.floor((item.received / item.size) * 100)}%` }}
                />
              </div>
              <div className="progress-file">
                {formatBytes(item.received)} of {formatBytes(item.size)} was uploaded before it stopped.
                choose the same file again to continue from there.
              </div>
              <div className="upload-actions">
                <button type="button" className="upload-action" onClick={() => pickerRef.current?.click()}>
                  choose file
                </button>
                <button type="button" className="upload-action" onClick={() => onDiscardUnfinished?.(item.upload_id)}>
                  discard
                </button>
              </div>
            </div>
          ))}
          <input
            ref={pickerRef}
            type="file"
            multiple
            accept=".pdf"
            className="hidden-file-input"
            onChange={(e) => {
              if (e.target.files?.length) onPickFiles?.(e.target.files);
              e.target.value = '';
            }}
          />
        </div>
      )}
    </div>
  );
}

export default UploadQueue;
