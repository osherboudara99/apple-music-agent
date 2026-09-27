// argv: names of playlists/folders to delete. Prints how many were deleted.
function run(argv) {
  const Music = Application('Music');
  let deleted = 0;
  for (const name of argv) {
    const hits = Music.playlists.whose({name: name});
    for (let i = hits.length - 1; i >= 0; i -= 1) { Music.delete(hits[i]); deleted += 1; }
  }
  return String(deleted);
}
