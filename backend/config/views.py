from django.conf import settings
from django.http import FileResponse
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie


@ensure_csrf_cookie
def index(request):
    """Sert le SPA React buildé (spa/index.html) ; retombe sur l'ancien template
    Django si le build n'est pas présent (dev sans build)."""
    spa_index = settings.SPA_DIR / "index.html"
    if spa_index.exists():
        return FileResponse(open(spa_index, "rb"), content_type="text/html")
    return render(request, "index.html")
