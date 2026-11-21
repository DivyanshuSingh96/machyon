# Turns a GitHub "release published" event into a Discord webhook message: the release title as a link, the
# release notes, the pip command and links. Used by .github/workflows/discord-release.yml.
#
# Discord's limits: title 256 characters, description 4096, field value 1024, 6000 for the whole embed.
# Long notes are cut at a line break with a link to the rest. allowed_mentions is empty, so an "@everyone" in the
# notes is shown as text and never pings anyone.

def clip($n): if length > $n then .[0:$n - 1] + "…" else . end;

.release as $r
| .repository.full_name as $repo
| ($r.tag_name | ltrimstr("v")) as $version
| (($r.body // "") | gsub("\r\n"; "\n") | sub("^\\s+"; "") | sub("\\s+$"; "")) as $body
| (if ($body | length) > 3900
   then ($body[0:3900] | sub("\n[^\n]*$"; "")) + "\n\n*The notes go on: [read them in full](" + $r.html_url + ").*"
   elif $body == "" then "No notes for this release."
   else $body end) as $notes
| {
    username: "Machyon releases",
    avatar_url: "https://divyanshusingh96.github.io/machyon/assets/brand/icon-512.png",
    allowed_mentions: {parse: []},
    embeds: [{
      title: (($r.name // "") | if . == "" then "Machyon " + $version else . end | clip(256)),
      url: $r.html_url,
      description: $notes,
      color: 9347839,
      fields: [
        {name: "Install", value: ("```\npip install -U machyon==" + $version + "\n```")},
        {name: "Links", value: ("[PyPI](https://pypi.org/project/machyon/" + $version + "/) · "
                               + "[Documentation](https://divyanshusingh96.github.io/machyon/) · "
                               + "[Changelog](https://github.com/" + $repo + "/blob/main/CHANGELOG.md)")}
      ],
      footer: {text: "Machyon", icon_url: "https://divyanshusingh96.github.io/machyon/assets/brand/icon-192.png"},
      timestamp: $r.published_at
    }]
  }
