import { useEffect, useState } from "react";
import { ImageOff } from "lucide-react";

export interface FaceCropImageProps {
  /** Backend URL; should include `?crop=1` for face-centered thumbnails. */
  thumbnailUrl: string;
  alt: string;
  className?: string;
}

/**
 * Circular avatar preview backed by server-side face crops (bounding box aware).
 */
export function FaceCropImage({
  thumbnailUrl,
  alt,
  className = "h-full w-full object-cover",
}: FaceCropImageProps): JSX.Element {
  const [loadFailed, setLoadFailed] = useState<boolean>(false);

  useEffect(() => {
    setLoadFailed(false);
  }, [thumbnailUrl]);

  if (loadFailed) {
    return (
      <div className="flex h-full w-full items-center justify-center bg-slate-100 text-slate-400">
        <ImageOff className="h-6 w-6" aria-hidden="true" />
      </div>
    );
  }

  return (
    <img
      src={thumbnailUrl}
      alt={alt}
      className={className}
      onError={() => setLoadFailed(true)}
    />
  );
}
