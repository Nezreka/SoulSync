"""The name SoulSync gives when it fetches a file from someone else's server.

Image hosts refuse the default library names ("python-requests", "Python-urllib"):
Discogs' image host answers 403 to them and 200 to an app that says who it is
(#2079). One constant so every image fetch is the same client.
"""

APP_USER_AGENT = "SoulSync/1.0 +https://github.com/Nezreka/SoulSync"

__all__ = ["APP_USER_AGENT"]
