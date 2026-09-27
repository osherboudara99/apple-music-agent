// argv[0]: JSON {name, fallback_name, folder, description, track_ids}
function run(argv) {
  const req = JSON.parse(argv[0]);
  const Music = Application('Music');
  const lib = Music.libraryPlaylists[0];
  const exists = (n) => Music.playlists.whose({name: n}).length > 0;

  let name = req.name;
  if (exists(name)) {
    name = req.fallback_name;
    let i = 2;
    while (exists(name)) { name = `${req.fallback_name} ${i}`; i += 1; }
  }

  let folder = null;
  const candidates = Music.playlists.whose({name: req.folder});
  for (let i = 0; i < candidates.length; i += 1) {
    if (candidates[i].class() === 'folderPlaylist') { folder = candidates[i]; break; }
  }
  if (!folder) folder = Music.make({new: 'folderPlaylist', withProperties: {name: req.folder}});

  const playlist = Music.make({new: 'playlist', at: folder, withProperties: {name: name}});
  const missing = [];
  try {
    if (req.description) {
      try { playlist.description = req.description; } catch (e) { /* not supported: ignore */ }
    }
    for (const id of req.track_ids) {
      const hits = lib.tracks.whose({persistentID: id});
      if (hits.length === 0) { missing.push(id); continue; }
      Music.duplicate(hits[0], {to: playlist});
    }
  } catch (e) {
    Music.delete(playlist); // never leave a half-built playlist behind
    throw e;
  }
  return JSON.stringify({
    name: name,
    persistent_id: playlist.persistentID(),
    track_count: playlist.tracks.length,
    missing_ids: missing,
  });
}
