// argv: [persistent id of a playlist this run just created, the agent's folder name].
// Deletes only that exact playlist, and only if it sits in the agent's folder. Prints the count.
function run(argv) {
  const [pid, folderName] = argv;
  const Music = Application('Music');
  const hits = Music.playlists.whose({persistentID: pid});
  if (hits.length === 0) return '0';
  const playlist = hits[0];
  let parent = null;
  try { parent = playlist.parent.name(); } catch (e) { /* top level */ }
  if (parent !== folderName) throw new Error('refusing to delete a playlist outside ' + folderName);
  Music.delete(playlist);
  return '1';
}
