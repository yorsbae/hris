"""Alamat situs publik. Dua pohon bernama `id:` (tanpa awalan) dan `en:` (awalan /en/) berbagi view yang sama; bahasa diambil dari namespace."""
from django.urls import include, path
from . import public_views as v

patterns = [
    path("", v.home, name="home"),
    path("produk/", v.products, name="products"),
    path("produk/<slug:slug>/", v.product, name="product"),
    path("lookbook/", v.lookbook, name="looks"),
    path("tentang-kami/", v.about, name="about"),
    path("halaman/<slug:slug>/", v.page, name="page"),
    path("lokasi-toko/", v.stores, name="stores"),
    path("artikel/", v.articles, name="articles"),
    path("artikel/<slug:slug>/", v.article, name="article"),
    path("arsip/", v.archive, name="archive"),
    path("faq/", v.faq, name="faq"),
    path("kontak/", v.contact, name="contact"),
    path("kontak/terkirim/", v.contact_done, name="contact_done"),
    path("cari/", v.search, name="search"),
    path("karir/", v.careers_list, name="careers"),
    path("karir/terkirim/", v.apply_done, name="apply_done"),
    path("karir/lupa-tautan/", v.lookup, name="lookup"),
    path("karir/status/<str:token>/", v.status, name="status"),
    path("karir/status/<str:token>/data.json", v.status_data, name="status_data"),
    path("karir/<slug:slug>/", v.job, name="job"),
    path("karir/<slug:slug>/lamar/", v.apply, name="apply"),
]

urlpatterns = [
    path("media/<path:name>", v.media_file, name="company_media"),
    path("favicon.ico", v.favicon),
    path("robots.txt", v.robots),
    path("sitemap.xml", v.sitemap),
    path("healthz", v.healthz),
    path("en/", include((patterns, "site"), namespace="en")),
    path("", include((patterns, "site"), namespace="id")),
]
