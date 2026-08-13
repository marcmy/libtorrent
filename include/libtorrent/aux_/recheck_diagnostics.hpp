/*

Copyright (c) 2026
All rights reserved.

You may use, distribute and modify this code under the terms of the BSD license,
see LICENSE file.
*/

#ifndef TORRENT_RECHECK_DIAGNOSTICS_HPP_INCLUDED
#define TORRENT_RECHECK_DIAGNOSTICS_HPP_INCLUDED

#include <cstdint>
#include <cstdlib>
#include <fstream>
#include <mutex>
#include <sstream>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

namespace libtorrent::aux::recheck_diag {

struct segment_trace
{
int block = -1;
int block_offset = 0;
int file = -1;
std::int64_t file_offset = 0;
std::int64_t requested = 0;
int returned = 0;
int error = 0;
int operation = 0;
std::string path;
std::string read_path;
};

struct piece_trace
{
std::string backend;
std::vector<segment_trace> segments;
};

inline std::string const& log_path()
{
static std::string const path = [] {
char const* const value =
std::getenv("LIBTORRENT_RECHECK_DIAGNOSTICS");

if (value == nullptr || *value == '\0'
|| std::string(value) == "0")
return std::string{};

if (std::string(value) == "1")
return std::string{"libtorrent-recheck-diagnostics.log"};

return std::string{value};
}();

return path;
}

inline bool enabled()
{
return !log_path().empty();
}

inline std::mutex& trace_mutex()
{
static std::mutex m;
return m;
}

inline std::unordered_map<std::uint64_t, piece_trace>& traces()
{
static std::unordered_map<std::uint64_t, piece_trace> t;
return t;
}

inline std::uint64_t trace_key(
std::uint32_t const storage, int const piece)
{
return (std::uint64_t(storage) << 32)
| std::uint32_t(piece);
}

inline std::string quoted(std::string const& value)
{
std::string ret;
ret.reserve(value.size() + 2);
ret.push_back('"');

for (char const c : value)
{
if (c == '"' || c == '\\')
ret.push_back('\\');

if (c == '\r')
{
ret += "\\r";
continue;
}

if (c == '\n')
{
ret += "\\n";
continue;
}

ret.push_back(c);
}

ret.push_back('"');
return ret;
}

inline void record_segment(
std::uint32_t const storage
, int const piece
, char const* const backend
, int const block
, int const block_offset
, int const file
, std::int64_t const file_offset
, std::int64_t const requested
, int const returned
, int const error
, int const operation
, std::string path
, char const* const read_path)
{
if (!enabled()) return;

std::lock_guard<std::mutex> l(trace_mutex());

auto& all = traces();

// checking_mem_usage bounds the number of in-flight checking pieces.
// This is only a final defensive ceiling.
if (all.size() > 1024)
all.clear();

auto& trace = all[trace_key(storage, piece)];

if (trace.backend.empty())
trace.backend = backend;

trace.segments.push_back({
block
, block_offset
, file
, file_offset
, requested
, returned
, error
, operation
, std::move(path)
, read_path
});
}

inline void finish_piece(
std::uint32_t const storage
, int const piece
, bool const failed
, std::string const& torrent_name
, std::string const& expected
, std::string const& actual
, bool const v1_failed
, bool const v2_failed
, int const error
, std::string const& error_message
, int const operation
, int const error_file)
{
if (!enabled()) return;

piece_trace trace;

{
std::lock_guard<std::mutex> l(trace_mutex());

auto& all = traces();
auto const i = all.find(trace_key(storage, piece));

if (i != all.end())
{
trace = std::move(i->second);
all.erase(i);
}
}

// Normal successful pieces never touch the filesystem.
if (!failed) return;

std::ostringstream out;

out << "RECHECK_FAILURE_BEGIN"
<< ",storage=" << storage
<< ",piece=" << piece
<< ",backend=" << trace.backend
<< ",torrent=" << quoted(torrent_name)
<< ",expected_sha1=" << expected
<< ",actual_sha1=" << actual
<< ",v1_failed=" << (v1_failed ? 1 : 0)
<< ",v2_failed=" << (v2_failed ? 1 : 0)
<< ",error=" << error
<< ",error_message=" << quoted(error_message)
<< ",operation=" << operation
<< ",error_file=" << error_file
<< '\n';

for (auto const& s : trace.segments)
{
out << "READ"
<< ",piece=" << piece
<< ",block=" << s.block
<< ",block_offset=" << s.block_offset
<< ",file=" << s.file
<< ",file_offset=" << s.file_offset
<< ",requested=" << s.requested
<< ",returned=" << s.returned
<< ",error=" << s.error
<< ",operation=" << s.operation
<< ",read_path=" << s.read_path
<< ",path=" << quoted(s.path)
<< '\n';
}

out << "RECHECK_FAILURE_END,piece=" << piece << '\n';

std::lock_guard<std::mutex> l(trace_mutex());

std::ofstream file(
log_path(),
std::ios::out | std::ios::app | std::ios::binary);

if (file)
file << out.str();
}

} // namespace libtorrent::aux::recheck_diag

#endif