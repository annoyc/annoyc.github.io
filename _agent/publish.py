#!/usr/bin/env python3
"""Publish a Markdown post into the static Hexo/NexT site in this repo.

The repo only holds Hexo's build output, so this script renders a Markdown
source (with front matter) into the same HTML shape NexT produces and
updates the pages that list posts:

  - /YYYY/MM/DD/<slug>/index.html   the post itself
  - /agent/index.html               list of every Agent post
  - /index.html                     newest Agent posts pinned on top
  - /archives/index.html            Agent posts in the archive timeline
  - /search.xml                     local search index
  - nav menu of every page          an "Agent" entry

Every generated page is rebuilt from _agent/posts/*.md, so running it again
is safe and simply regenerates everything.

Usage:
    pip install markdown   # once
    python3 _agent/publish.py
"""
import html
import json
import os
import re
import sys
from datetime import datetime
from urllib.parse import quote

try:
    import markdown
except ImportError:
    sys.exit("missing dependency: pip install markdown")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENT_DIR = os.path.join(ROOT, "_agent")
POSTS_DIR = os.path.join(AGENT_DIR, "posts")
TPL_POST = os.path.join(AGENT_DIR, "templates", "post.html")
TPL_LIST = os.path.join(AGENT_DIR, "templates", "list.html")
SITE_URL = "http://yc-sky.top"
SITE_NAME = "Yc的博客"
LIST_TITLE = "Agent 日课"
INDEX_PINNED = 5          # how many Agent posts to pin on the home page
LEGACY_POST_COUNT = 30    # posts that existed before this script

MENU_ITEM = (
    '<li class="menu-item menu-item-agent">\n'
    '          <a href="/agent/" rel="section">\n'
    '              <i class="menu-item-icon fa fa-fw fa-lightbulb-o"></i> <br />\n'
    '            Agent\n'
    '          </a>\n'
    '        </li>\n'
    '        '
)

START, END = "<!--agent-daily:start-->", "<!--agent-daily:end-->"


# --------------------------------------------------------------------------
# reading posts
# --------------------------------------------------------------------------
def parse_front_matter(text):
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    if not m:
        raise ValueError("front matter missing")
    meta = {}
    for line in m.group(1).splitlines():
        if not line.strip() or ":" not in line:
            continue
        k, v = line.split(":", 1)
        v = v.strip()
        if v.startswith("[") and v.endswith("]"):
            v = [x.strip().strip("'\"") for x in v[1:-1].split(",") if x.strip()]
        else:
            v = v.strip("'\"")
        meta[k.strip()] = v
    return meta, text[m.end():]


def load_posts():
    posts = []
    for name in sorted(os.listdir(POSTS_DIR)):
        if not name.endswith(".md"):
            continue
        with open(os.path.join(POSTS_DIR, name), encoding="utf-8") as f:
            meta, body = parse_front_matter(f.read())
        for key in ("title", "slug", "date"):
            if key not in meta:
                raise ValueError(f"{name}: front matter needs '{key}'")
        if not re.fullmatch(r"[a-z0-9-]+", meta["slug"]):
            raise ValueError(f"{name}: slug must be lowercase letters, digits and '-'")
        dt = datetime.strptime(meta["date"], "%Y-%m-%d %H:%M:%S")
        tags = meta.get("tags") or ["Agent"]
        if isinstance(tags, str):
            tags = [tags]
        path = f"{dt:%Y/%m/%d}/{meta['slug']}/"
        posts.append({
            "file": name,
            "title": meta["title"],
            "slug": meta["slug"],
            "date": dt,
            "tags": tags,
            "excerpt": meta.get("excerpt", ""),
            "path": path,
            "url": "/" + path,
            "body_md": body,
        })
    posts.sort(key=lambda p: p["date"], reverse=True)
    return posts


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------
def slugify_heading(value, sep):
    value = re.sub(r"[^\w一-鿿\s-]", "", value).strip()
    return re.sub(r"[\s]+", "-", value)


def render_code(m):
    lang = m.group(1) or "plain"
    code = m.group(2)  # already HTML-escaped by markdown
    lines = code.rstrip("\n").split("\n")
    gutter = "".join(f'<span class="line">{i}</span><br>' for i in range(1, len(lines) + 1))
    body = "".join(f'<span class="line">{l}</span><br>' for l in lines)
    return (f'<figure class="highlight {lang}"><table><tr>'
            f'<td class="gutter"><pre>{gutter}</pre></td>'
            f'<td class="code"><pre>{body}</pre></td></tr></table></figure>')


def render_body(md_text):
    md = markdown.Markdown(
        extensions=["fenced_code", "tables", "toc", "sane_lists"],
        extension_configs={"toc": {"slugify": slugify_heading}},
    )
    out = md.convert(md_text)
    # Hexo-style heading anchors
    out = re.sub(
        r'<h([2-4]) id="([^"]+)">(.*?)</h\1>',
        lambda m: (f'<h{m.group(1)} id="{m.group(2)}"><a href="#{m.group(2)}" '
                   f'class="headerlink" title="{html.escape(re.sub("<[^>]+>", "", m.group(3)))}"></a>'
                   f'{m.group(3)}</h{m.group(1)}>'),
        out,
    )
    # Hexo-style highlighted code blocks
    out = re.sub(
        r'<pre><code(?: class="language-([\w+-]+)")?>(.*?)</code></pre>',
        render_code, out, flags=re.S,
    )
    # external links open in a new tab, like the rest of the site
    out = re.sub(r'<a href="(https?://[^"]+)">',
                 r'<a href="\1" target="_blank" rel="noopener">', out)
    # "read more" marker after the first paragraph
    out = out.replace("</p>", '</p>\n<a id="more"></a>', 1)
    return out, md.toc_tokens


def render_toc(tokens, prefix=""):
    if not tokens:
        return ""
    items = []
    for i, t in enumerate(tokens, 1):
        num = f"{prefix}{i}."
        child = render_toc(t["children"], num)
        items.append(
            f'<li class="nav-item nav-level-{t["level"]}"><a class="nav-link" href="#{t["id"]}">'
            f'<span class="nav-number">{num}</span> <span class="nav-text">{html.escape(t["name"])}</span></a>'
            f'{child}</li>')
    cls = "nav" if not prefix else "nav-child"
    return f'<ol class="{cls}">' + "".join(items) + "</ol>"


def plain_text(html_text):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", html_text))).strip()


def enc(path):
    return quote(path, safe="/")


def iso_utc(dt):
    # dates in front matter are Beijing time
    from datetime import timedelta
    return (dt - timedelta(hours=8)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def sub_once(pattern, repl, text, flags=re.S):
    new, n = re.subn(pattern, lambda m: repl, text, count=1, flags=flags)
    if n != 1:
        raise RuntimeError(f"template pattern not found: {pattern[:60]}")
    return new


# --------------------------------------------------------------------------
# pages
# --------------------------------------------------------------------------
def build_post(post, newer, older, tpl):
    body_html, toc_tokens = render_body(post["body_md"])
    post["html"] = body_html
    desc = post["excerpt"] or plain_text(body_html.split('<a id="more"></a>')[0])
    post["excerpt"] = desc
    title = html.escape(post["title"])
    d = html.escape(desc)
    url = SITE_URL + enc(post["url"])

    s = tpl
    s = sub_once(r'<meta name="keywords" content="[^"]*" />',
                 f'<meta name="keywords" content="{html.escape(",".join(post["tags"]))}," />', s)
    s = sub_once(r'<meta name="description" content="[^"]*">', f'<meta name="description" content="{d}">', s)
    s = sub_once(r'<meta property="og:title" content="[^"]*">', f'<meta property="og:title" content="{title}">', s)
    s = sub_once(r'<meta property="og:url" content="[^"]*">', f'<meta property="og:url" content="{url}index.html">', s)
    s = sub_once(r'<meta property="og:description" content="[^"]*">', f'<meta property="og:description" content="{d}">', s)
    s = sub_once(r'<meta property="article:published_time" content="[^"]*">',
                 f'<meta property="article:published_time" content="{iso_utc(post["date"])}">', s)
    s = sub_once(r'<meta property="article:modified_time" content="[^"]*">',
                 f'<meta property="article:modified_time" content="{iso_utc(post["date"])}">', s)
    s = sub_once(r'<meta property="article:tag" content="[^"]*">',
                 "\n".join(f'<meta property="article:tag" content="{html.escape(t)}">' for t in post["tags"]), s)
    s = sub_once(r'<link rel="canonical" href="[^"]*"/>', f'<link rel="canonical" href="{SITE_URL}{post["url"]}"/>', s)
    s = sub_once(r"<title>.*?</title>", f"<title>{title} | {SITE_NAME}</title>", s)
    s = sub_once(r'<link itemprop="mainEntityOfPage" href="[^"]*">',
                 f'<link itemprop="mainEntityOfPage" href="{url}">', s)
    s = sub_once(r'<h1 class="post-title" itemprop="name headline">.*?</h1>',
                 f'<h1 class="post-title" itemprop="name headline">{title}</h1>', s)
    s = sub_once(r'<div class="post-body" itemprop="articleBody">.*?<footer class="post-footer">',
                 f'<div class="post-body" itemprop="articleBody">\n{body_html}\n    </div>\n\n    <footer class="post-footer">', s)

    tags = "\n".join(f'          <a href="/agent/" rel="tag"># {html.escape(t)}</a>' for t in post["tags"])
    nav_next = nav_prev = ""
    if older:
        nav_next = (f'<a href="{enc(older["url"])}" rel="next" title="{html.escape(older["title"])}">\n'
                    f'                <i class="fa fa-chevron-left"></i> {html.escape(older["title"])}\n              </a>')
    if newer:
        nav_prev = (f'<a href="{enc(newer["url"])}" rel="prev" title="{html.escape(newer["title"])}">\n'
                    f'                {html.escape(newer["title"])} <i class="fa fa-chevron-right"></i>\n              </a>')
    footer = (
        '<footer class="post-footer">\n'
        f'        <div class="post-tags">\n{tags}\n        </div>\n'
        '        <div class="post-nav">\n'
        f'          <div class="post-nav-next post-nav-item">\n              {nav_next}\n          </div>\n'
        '          <span class="post-nav-divider"></span>\n'
        f'          <div class="post-nav-prev post-nav-item">\n              {nav_prev}\n          </div>\n'
        '        </div>\n'
        '    </footer>'
    )
    s = sub_once(r'<footer class="post-footer">.*?</footer>', footer, s)
    toc = render_toc(toc_tokens) or '<ol class="nav"></ol>'
    s = sub_once(r'<div class="post-toc-content">.*?</ol></div>', f'<div class="post-toc-content">{toc}</div>', s)

    out = os.path.join(ROOT, post["path"], "index.html")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(s)


def card(post):
    u = enc(post["url"])
    return f'''  <article class="post post-type-normal" itemscope itemtype="http://schema.org/Article">
  <div class="post-block">
    <link itemprop="mainEntityOfPage" href="{SITE_URL}{u}">
    <span hidden itemprop="publisher" itemscope itemtype="http://schema.org/Organization">
      <meta itemprop="name" content="{SITE_NAME}">
    </span>
      <header class="post-header">
          <h1 class="post-title" itemprop="name headline">
                <a class="post-title-link" href="{u}" itemprop="url">{html.escape(post["title"])}</a></h1>
          <div class="post-meta">
            <span class="post-time">
              <span class="post-meta-item-icon"><i class="fa fa-calendar-o"></i></span>
              <time title="发表于" itemprop="dateCreated datePublished" datetime="{post["date"]:%Y-%m-%dT%H:%M:%S}+08:00">{post["date"]:%Y-%m-%d}</time>
            </span>
          </div>
      </header>
    <div class="post-body" itemprop="articleBody">
          <p>{html.escape(post["excerpt"])}</p>
          <!--noindex-->
          <div class="post-button text-center">
            <a class="btn" href="{u}#more" rel="contents">
              阅读全文 &raquo;
            </a>
          </div>
          <!--/noindex-->
    </div>
    <footer class="post-footer">
        <div class="post-eof"></div>
    </footer>
  </div>
  </article>
'''


def build_list_page(posts, tpl):
    s = tpl
    s = sub_once(r'<section id="posts" class="posts-expand">.*?</section>',
                 '<section id="posts" class="posts-expand">\n' + "".join(card(p) for p in posts) + "  </section>", s)
    s = sub_once(r'\s*<nav class="pagination">.*?</nav>', "\n", s)
    s = sub_once(r"<title>.*?</title>", f"<title>{LIST_TITLE} | {SITE_NAME}</title>", s)
    s = re.sub(r'<link rel="canonical" href="[^"]*"/>', f'<link rel="canonical" href="{SITE_URL}/agent/"/>', s, count=1)
    out = os.path.join(ROOT, "agent", "index.html")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(s)


def replace_block(s, content, anchor_pattern):
    """Replace the marked block, or insert it right after anchor_pattern."""
    block = f"{START}\n{content}{END}\n"
    if START in s:
        return re.sub(re.escape(START) + ".*?" + re.escape(END) + r"\n?", lambda m: block, s, count=1, flags=re.S)
    m = re.search(anchor_pattern, s, re.S)
    if not m:
        raise RuntimeError(f"anchor not found: {anchor_pattern}")
    return s[:m.end()] + "\n" + block + s[m.end():]


def update_file(rel, fn):
    p = os.path.join(ROOT, rel)
    with open(p, encoding="utf-8") as f:
        s = f.read()
    new = fn(s)
    if new != s:
        with open(p, "w", encoding="utf-8") as f:
            f.write(new)


def archive_entry(post):
    return f'''  <article class="post post-type-normal" itemscope itemtype="http://schema.org/Article">
    <header class="post-header">
      <h2 class="post-title">
            <a class="post-title-link" href="{enc(post["url"])}" itemprop="url">
                <span itemprop="name">{html.escape(post["title"])}</span>
            </a>
      </h2>
      <div class="post-meta">
        <time class="post-time" itemprop="dateCreated"
              datetime="{post["date"]:%Y-%m-%dT%H:%M:%S}+08:00"
              content="{post["date"]:%Y-%m-%d}" >
          {post["date"]:%m-%d}
        </time>
      </div>
    </header>
  </article>
'''


def build_archives(s, posts):
    parts, year = [], None
    for p in posts:
        if p["date"].year != year:
            year = p["date"].year
            parts.append(f'          <div class="collection-title">\n'
                         f'            <h1 class="archive-year" id="archive-year-{year}-agent">{year}</h1>\n'
                         f'          </div>\n')
        parts.append(archive_entry(p))
    s = replace_block(s, "".join(parts), r'<span class="archive-page-counter">.*?</span>')
    return re.sub(r"目前共计 \d+ 篇日志", f"目前共计 {LEGACY_POST_COUNT + len(posts)} 篇日志", s)


def build_search(s, posts):
    entries = []
    for p in posts:
        entries.append(
            "  <entry>\n"
            f"    <title>{html.escape(p['title'])}</title>\n"
            f"    <url>{enc(p['url'])}</url>\n"
            f"    <content><![CDATA[{p['html'].replace(']]>', ']]]]><![CDATA[>')}]]></content>\n"
            "    <categories>\n      <category>Agent</category>\n    </categories>\n"
            "    <tags>\n" + "".join(f"      <tag>{html.escape(t)}</tag>\n" for t in p["tags"]) + "    </tags>\n"
            "  </entry>\n")
    return replace_block(s, "".join(entries), r"<search>")


def add_menu_everywhere():
    for dirpath, dirnames, files in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if not d.startswith((".", "_")) and d != "lib"]
        for name in files:
            if not name.endswith(".html"):
                continue
            def fn(s):
                if "menu-item-agent" in s:
                    return s
                return s.replace('<li class="menu-item menu-item-tags">', MENU_ITEM + '<li class="menu-item menu-item-tags">', 1)
            update_file(os.path.relpath(os.path.join(dirpath, name), ROOT), fn)


def main():
    posts = load_posts()
    if not posts:
        sys.exit("no posts in _agent/posts")
    with open(TPL_POST, encoding="utf-8") as f:
        tpl_post = f.read()
    with open(TPL_LIST, encoding="utf-8") as f:
        tpl_list = f.read()

    for i, p in enumerate(posts):
        newer = posts[i - 1] if i > 0 else None
        older = posts[i + 1] if i + 1 < len(posts) else None
        build_post(p, newer, older, tpl_post)
    build_list_page(posts, tpl_list)

    update_file("index.html", lambda s: replace_block(
        s, "".join(card(p) for p in posts[:INDEX_PINNED]), r'<section id="posts" class="posts-expand">'))
    update_file("archives/index.html", lambda s: build_archives(s, posts))
    update_file("search.xml", lambda s: build_search(s, posts))
    add_menu_everywhere()

    with open(os.path.join(AGENT_DIR, "index.json"), "w", encoding="utf-8") as f:
        json.dump([{"title": p["title"], "date": p["date"].strftime("%Y-%m-%d"), "url": p["url"],
                    "tags": p["tags"], "file": p["file"]} for p in posts], f, ensure_ascii=False, indent=2)
    print(f"published {len(posts)} post(s); newest: {posts[0]['title']} -> {posts[0]['url']}")


if __name__ == "__main__":
    main()
