"""Medium editor CSS/role selectors.

Kept in one file so selector drift is a single-file fix.
Last verified: 2026-05-11
"""

# Title editable area in the Medium story editor
TITLE = 'h3[data-testid="storyTitle"]'

# Body editable area (ProseMirror / contenteditable)
BODY = '.pw-editor-frame >> div[contenteditable="true"]'

# Publish/settings button in the top bar
PUBLISH_MENU = 'button[data-testid="publishButton"]'

# "Publish now" within the publish dialog
PUBLISH_BUTTON = 'button[data-testid="publishNowButton"]'

# "Save draft" / "Done" to save as draft
SAVE_DRAFT = 'button[data-testid="saveDraftButton"]'

# Tag input inside the publish dialog
TAGS_INPUT = 'input[data-testid="tagInput"]'

# Login redirect indicator
LOGIN_PATH = "/m/signin"

# CAPTCHA iframe indicator
CAPTCHA_IFRAME_SELECTOR = "iframe[src*='captcha']"
