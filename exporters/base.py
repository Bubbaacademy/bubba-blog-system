from abc import ABC, abstractmethod


class BaseExporter(ABC):
    """
    Abstract base for all content exporters.
    To add a new CMS (WordPress, HubSpot, Webflow, etc.),
    create a new file in exporters/ and subclass this.
    """

    @abstractmethod
    def export(self, row: dict, content: dict) -> dict:
        """
        Export content for one row.

        Args:
            row:     The full row data from Google Sheets (all columns).
            content: Dict with keys: seo_title, meta_description,
                     blog_article, social_caption, video_script, email_copy.

        Returns:
            Dict with at minimum:
                success (bool)
                message (str)
            Plus any exporter-specific keys (e.g. doc_url, file_path).
        """
        pass

    @abstractmethod
    def name(self) -> str:
        """Human-readable name for logging."""
        pass
