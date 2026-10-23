
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS books (id TEXT PRIMARY KEY, title TEXT, author TEXT, added TEXT, manifest TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS generations (id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE, name TEXT, created TEXT, updated TEXT, status TEXT, config TEXT, cast TEXT NOT NULL DEFAULT '[]', analysis TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '', parent_id TEXT);
CREATE TABLE IF NOT EXISTS segments (generation_id TEXT NOT NULL REFERENCES generations(id) ON DELETE CASCADE, id TEXT NOT NULL, ordinal INTEGER, chapter_id TEXT, source_id TEXT, start INTEGER, end INTEGER, text TEXT, spoken TEXT, speaker TEXT NOT NULL DEFAULT 'narrator', delivery TEXT NOT NULL DEFAULT 'neutral', status TEXT NOT NULL DEFAULT 'pending', audio_hash TEXT, duration REAL NOT NULL DEFAULT 0, cues TEXT NOT NULL DEFAULT '[]', warning TEXT NOT NULL DEFAULT '', PRIMARY KEY(generation_id,id));
CREATE INDEX IF NOT EXISTS segments_order ON segments(generation_id,ordinal);
CREATE TABLE IF NOT EXISTS positions (book_id TEXT PRIMARY KEY REFERENCES books(id) ON DELETE CASCADE, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS bookmarks (id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE, source_id TEXT, note TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS visuals (id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE, source_id TEXT, caption TEXT, file TEXT, kind TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS exports (id TEXT PRIMARY KEY, generation_id TEXT NOT NULL REFERENCES generations(id) ON DELETE CASCADE, file TEXT, format TEXT, created TEXT);
PRAGMA user_version=1;
