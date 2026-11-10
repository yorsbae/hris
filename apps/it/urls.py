from django.urls import path
from . import views as v, inv_views as iv

_a = v._spec("assets"); _l = v._spec("licenses"); _d = v._spec("domains"); _t = v._spec("tickets")
urlpatterns = [
    path("", v.home),
    path("assets/", _a[0]), path("assets/new/", _a[1]), path("assets/<int:pk>/", v.asset_detail), path("assets/<int:pk>/ubah/", _a[1]), path("assets/<int:pk>/hapus/", _a[2]),
    path("licenses/", _l[0]), path("licenses/new/", _l[1]), path("licenses/<int:pk>/", _l[1]), path("licenses/<int:pk>/hapus/", _l[2]),
    path("domains/", _d[0]), path("domains/new/", _d[1]), path("domains/<int:pk>/", _d[1]), path("domains/<int:pk>/hapus/", _d[2]),
    path("tickets/", _t[0]), path("tickets/new/", _t[1]), path("tickets/<int:pk>/", _t[1]), path("tickets/<int:pk>/hapus/", _t[2]),
    path("ip/", v.ip_home), path("ip/new/", v.subnet_edit), path("ip/<int:pk>/", v.subnet_detail), path("ip/<int:pk>/ubah/", v.subnet_edit),
]
urlpatterns += [
    path("stock/", iv.stock_list), path("stock/new/", iv.stock_edit), path("stock/moves/", iv.moves_list), path("stock/<int:pk>/", iv.stock_detail), path("stock/<int:pk>/ubah/", iv.stock_edit), path("stock/<int:pk>/hapus/", iv.stock_delete),
    path("wishlist/", iv.wish_list), path("wishlist/ajukan/", iv.wish_submit), path("wishlist/new/", iv.wish_edit), path("wishlist/<int:pk>/", iv.wish_detail), path("wishlist/<int:pk>/ubah/", iv.wish_edit), path("wishlist/<int:pk>/hapus/", iv.wish_delete),
    path("net/", iv.net_list), path("net/new/", iv.net_edit), path("net/<int:pk>/", iv.net_edit), path("net/<int:pk>/hapus/", iv.net_delete), path("net/<slug:group>/", iv.net_list),
]
