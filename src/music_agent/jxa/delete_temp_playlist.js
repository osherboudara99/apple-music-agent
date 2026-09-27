// argv[0]: the unique "music-agent building <id>" name of an interrupted build. Deletes only
// playlists with exactly that name (it can't match anything the user created).
function run(argv) {
  const name = argv[0];
  if (!name || name.indexOf('music-agent building ') !== 0) throw new Error('refusing to delete ' + name);
  const Music = Application('Music');
  const hits = Music.playlists.whose({name: name});
  let deleted = 0;
  for (let i = hits.length - 1; i >= 0; i -= 1) { Music.delete(hits[i]); deleted += 1; }
  return String(deleted);
}
