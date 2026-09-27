// argv[0]: JSON {name, fallback_name, temp_name, folder, description, track_ids}
// Builds under a unique temp_name and renames only when complete, so an interrupted run
// (e.g. a timeout) leaves at most a playlist whose name can't belong to anything else.
function run(argv) {
  const req = JSON.parse(argv[0]);
  const Music = Application('Music');
  const lib = Music.libraryPlaylists[0];
  const exists = (n) => Music.playlists.whose({name: n}).length > 0;

  let folder = null;
  const candidates = Music.playlists.whose({name: req.folder});
  for (let i = 0; i < candidates.length; i += 1) {
    if (candidates[i].class() === 'folderPlaylist') { folder = candidates[i]; break; }
  }
  if (!folder) folder = Music.make({new: 'folderPlaylist', withProperties: {name: req.folder}});

  const playlist = Music.make({new: 'playlist', at: folder, withProperties: {name: req.temp_name}});
  const missing = [];
  let name;
  try {
    for (const id of req.track_ids) {
      const hits = lib.tracks.whose({persistentID: id});
      if (hits.length === 0) { missing.push(id); continue; }
      Music.duplicate(hits[0], {to: playlist});
    }
    if (missing.length === req.track_ids.length) {
      throw new Error('None of the requested tracks are in your library.');
    }
    name = req.name;
    if (exists(name)) {
      name = req.fallback_name;
      let i = 2;
      while (exists(name)) { name = `${req.fallback_name} ${i}`; i += 1; }
    }
    playlist.name = name;
    if (req.description) {
      try { playlist.description = req.description; } catch (e) { /* not supported: ignore */ }
    }
  } catch (e) {
    Music.delete(playlist); // never leave a half-built or empty playlist behind
    throw e;
  }
  return JSON.stringify({
    name: name,
    persistent_id: playlist.persistentID(),
    track_count: playlist.tracks.length,
    missing_ids: missing,
  });
}
