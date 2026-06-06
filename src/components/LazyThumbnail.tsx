import { useEffect, useRef, useState } from "react";
import { PLACEHOLDER_THUMBNAIL_SRC } from "@/constants/placeholders";

export interface LazyThumbnailProps {
  src: string;
  alt: string;
  className?: string;
  onError?: () => void;
}

export function LazyThumbnail({
  src,
  alt,
  className,
  onError,
}: LazyThumbnailProps): JSX.Element {
  const elementRef = useRef<HTMLImageElement>(null);
  const [isVisible, setIsVisible] = useState<boolean>(false);

  useEffect(() => {
    const element = elementRef.current;
    if (element === null) {
      return;
    }

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry?.isIntersecting) {
          setIsVisible(true);
          observer.disconnect();
        }
      },
      { rootMargin: "200px" },
    );

    observer.observe(element);
    return () => {
      observer.disconnect();
    };
  }, []);

  return (
    <img
      ref={elementRef}
      src={isVisible ? src : PLACEHOLDER_THUMBNAIL_SRC}
      alt={alt}
      className={className}
      loading="lazy"
      onError={onError}
    />
  );
}
