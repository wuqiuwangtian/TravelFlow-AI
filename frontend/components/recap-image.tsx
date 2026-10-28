"use client";

import { useRef, useState } from "react";
import { Maximize2, X } from "lucide-react";
import { Button } from "@/components/ui";
import { api } from "@/lib/api";

type Props = {
  versionId: string;
  photoId: string;
  label: string;
  mode?: "cover" | "contain";
};
export function RecapImage(props: Props) {
  return (
    <PhotoResource key={`${props.versionId}:${props.photoId}`} {...props} />
  );
}
function PhotoResource({ versionId, photoId, label, mode = "cover" }: Props) {
  const [failed, setFailed] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [retry, setRetry] = useState(0);
  const [viewerFailed, setViewerFailed] = useState(false);
  const [viewing, setViewing] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const src =
    api.travelPhotoUrl(versionId, photoId) + (retry ? `?retry=${retry}` : "");
  if (failed)
    return (
      <div className="history-photo-fallback">
        <p>照片暂时无法显示，仍可阅读原回顾。</p>
        <Button
          type="button"
          size="sm"
          onClick={() => {
            setFailed(false);
            setLoaded(false);
            setRetry((value) => value + 1);
          }}
        >
          重试照片
        </Button>
      </div>
    );
  return (
    <div className="tf-recap-image" data-mode={mode}>
      <button
        ref={trigger}
        type="button"
        className="history-photo-trigger"
        disabled={!loaded}
        aria-busy={!loaded}
        aria-label={`放大照片：${label}`}
        onClick={() => {
          setViewerFailed(false);
          setViewing(true);
          dialog.current?.showModal();
        }}
      >
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={src}
          alt={label}
          loading="lazy"
          onLoad={() => setLoaded(true)}
          onError={() => setFailed(true)}
        />
        {loaded && (
          <span className="history-photo-zoom">
            <Maximize2 aria-hidden="true" className="h-4 w-4" />
            查看完整照片
          </span>
        )}
      </button>
      <dialog
        ref={dialog}
        className="history-photo-dialog"
        aria-label="查看完整旅行照片"
        onClose={() => {
          setViewing(false);
          trigger.current?.focus({ preventScroll: true });
        }}
        onClick={(event) => {
          if (event.target === event.currentTarget) event.currentTarget.close();
        }}
      >
        <div className="history-photo-viewer">
          <div className="history-photo-viewer-heading">
            <p>{label}</p>
            <Button
              type="button"
              size="sm"
              aria-label="关闭完整照片"
              onClick={() => dialog.current?.close()}
            >
              <X aria-hidden="true" className="h-4 w-4" />
              关闭
            </Button>
          </div>
          {viewing && !viewerFailed && (
            <>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={src}
                alt={label}
                onError={() => setViewerFailed(true)}
              />
            </>
          )}
          {viewerFailed && (
            <p role="alert">完整照片暂时无法显示，可以关闭后重试。</p>
          )}
        </div>
      </dialog>
    </div>
  );
}
