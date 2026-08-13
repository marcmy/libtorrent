/*
Copyright (c) 2026, marcmy

Use, distribution and modification are permitted under the BSD license used by
libtorrent. See the repository LICENSE file.
*/

#include "libtorrent/config.hpp"

#ifdef TORRENT_WINDOWS

#include "libtorrent/aux_/path.hpp"
#include "libtorrent/hasher.hpp"
#include "libtorrent/sha1_hash.hpp"
#include "libtorrent/span.hpp"

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <wofapi.h>

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <iomanip>
#include <iostream>
#include <limits>
#include <memory>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

namespace
{
    struct handle_closer
    {
        void operator()(void *const h) const
        {
            if (h && h != INVALID_HANDLE_VALUE)
                ::CloseHandle(static_cast<HANDLE>(h));
        }
    };

    using unique_handle = std::unique_ptr<void, handle_closer>;

    struct options
    {
        std::string file;
        int buffer_kib = 1024;
        int runs = 1;
    };

    [[noreturn]] void usage(char const *const program, int const exit_code)
    {
        std::ostream &out = (exit_code == 0) ? std::cout : std::cerr;
        out
            << "Usage:\n"
            << "  " << program << " --file <file> [options]\n\n"
            << "Options:\n"
            << "  --buffer-kib <KiB>       Read size per request (default: 1024)\n"
            << "  --runs <n>               Repetitions (default: 1)\n"
            << "  --help                    Show this help\n\n"
            << "The probe reports WOF backing state and hashes the complete file twice:\n"
            << "  sync   - normal synchronous ReadFile using the shared file pointer\n"
            << "  pread  - synchronous ReadFile with an OVERLAPPED byte offset, matching\n"
            << "           libtorrent's Windows explicit-offset pread path\n\n"
            << "Both modes are read-only. A digest or byte-count mismatch is evidence\n"
            << "that the WOF/filter-stack read path is not transparent to libtorrent's\n"
            << "access pattern. Machine-readable rows begin with WOFRESULT,.\n";
        std::exit(exit_code);
    }

    int positive_int(std::string const &value, std::string_view const name)
    {
        std::size_t consumed = 0;
        int const result = std::stoi(value, &consumed);
        if (consumed != value.size() || result <= 0)
            throw std::invalid_argument(std::string(name) + " must be a positive integer");
        return result;
    }

    options parse_options(int const argc, char const *const argv[])
    {
        options result;
        auto require_value = [&](int &index, std::string_view const name) -> std::string
        {
            if (++index >= argc)
                throw std::invalid_argument(std::string(name) + " requires a value");
            return argv[index];
        };

        for (int i = 1; i < argc; ++i)
        {
            std::string const arg = argv[i];
            if (arg == "--help" || arg == "-h") usage(argv[0], 0);
            else if (arg == "--file") result.file = require_value(i, arg);
            else if (arg == "--buffer-kib") result.buffer_kib = positive_int(require_value(i, arg), arg);
            else if (arg == "--runs") result.runs = positive_int(require_value(i, arg), arg);
            else throw std::invalid_argument("unknown argument: " + arg);
        }

        if (result.file.empty())
            throw std::invalid_argument("--file is required");
        if (result.buffer_kib > 1024 * 1024)
            throw std::invalid_argument("--buffer-kib is unreasonably large");
        return result;
    }

    unique_handle open_read_only(std::string const &path)
    {
        auto const native = libtorrent::convert_to_native_path_string(path);
        HANDLE const handle = ::CreateFileW(native.c_str(), GENERIC_READ
            , FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE
            , nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL | FILE_FLAG_SEQUENTIAL_SCAN, nullptr);
        if (handle == INVALID_HANDLE_VALUE)
            throw std::runtime_error("CreateFileW failed: " + std::to_string(::GetLastError()));
        return unique_handle(handle);
    }

    std::uint64_t file_size(HANDLE const file)
    {
        LARGE_INTEGER size {};
        if (!::GetFileSizeEx(file, &size))
            throw std::runtime_error("GetFileSizeEx failed: " + std::to_string(::GetLastError()));
        if (size.QuadPart < 0)
            throw std::runtime_error("negative file size");
        return static_cast<std::uint64_t>(size.QuadPart);
    }

    struct storage_sizes
    {
        std::uint64_t logical = 0;
        std::uint64_t allocation = 0;
    };

    storage_sizes query_storage_sizes(HANDLE const file)
    {
        FILE_STANDARD_INFO info {};
        if (!::GetFileInformationByHandleEx(file, FileStandardInfo, &info, sizeof(info)))
            throw std::runtime_error("GetFileInformationByHandleEx(FileStandardInfo) failed: "
                + std::to_string(::GetLastError()));
        return {static_cast<std::uint64_t>(info.EndOfFile.QuadPart)
            , static_cast<std::uint64_t>(info.AllocationSize.QuadPart)};
    }

    char const *algorithm_name(ULONG const algorithm)
    {
        switch (algorithm)
        {
        case FILE_PROVIDER_COMPRESSION_XPRESS4K: return "XPRESS4K";
        case FILE_PROVIDER_COMPRESSION_XPRESS8K: return "XPRESS8K";
        case FILE_PROVIDER_COMPRESSION_XPRESS16K: return "XPRESS16K";
        case FILE_PROVIDER_COMPRESSION_LZX: return "LZX";
        default: return "unknown";
        }
    }

    struct wof_state
    {
        bool external = false;
        ULONG provider = 0;
        ULONG algorithm = 0;
        HRESULT status = S_OK;
    };

    wof_state query_wof(std::string const &path)
    {
        auto const native = libtorrent::convert_to_native_path_string(path);
        BOOL external = FALSE;
        ULONG provider = 0;
        WOF_FILE_COMPRESSION_INFO_V1 info {};
        ULONG info_size = sizeof(info);
        HRESULT const hr = ::WofIsExternalFile(native.c_str(), &external, &provider, &info, &info_size);

        wof_state result;
        result.status = hr;
        if (SUCCEEDED(hr))
        {
            result.external = external != FALSE;
            result.provider = provider;
            if (result.external && provider == WOF_PROVIDER_FILE
                && info_size >= sizeof(WOF_FILE_COMPRESSION_INFO_V1))
                result.algorithm = info.Algorithm;
        }
        return result;
    }

    std::string digest_hex(libtorrent::sha1_hash const &digest)
    {
        static constexpr char hex[] = "0123456789abcdef";
        std::string result;
        result.reserve(static_cast<std::size_t>(digest.size() * 2));
        for (std::uint8_t const byte : digest)
        {
            result.push_back(hex[byte >> 4]);
            result.push_back(hex[byte & 0x0f]);
        }
        return result;
    }

    struct read_result
    {
        double seconds = 0.0;
        std::uint64_t bytes = 0;
        std::uint64_t short_reads = 0;
        libtorrent::sha1_hash digest;
    };

    read_result run_sync(std::string const &path, std::size_t const buffer_size)
    {
        unique_handle file = open_read_only(path);
        std::uint64_t const size = file_size(static_cast<HANDLE>(file.get()));
        std::vector<char> buffer(buffer_size);
        libtorrent::hasher hash;
        std::uint64_t total = 0;
        std::uint64_t short_reads = 0;

        auto const start = std::chrono::steady_clock::now();
        while (total < size)
        {
            DWORD const request = static_cast<DWORD>(std::min<std::uint64_t>(buffer.size(), size - total));
            DWORD read = 0;
            if (!::ReadFile(static_cast<HANDLE>(file.get()), buffer.data(), request, &read, nullptr))
                throw std::runtime_error("sync ReadFile failed at offset " + std::to_string(total)
                    + ": " + std::to_string(::GetLastError()));
            if (read == 0)
                break;
            if (read != request)
                ++short_reads;
            hash.update(libtorrent::span<char const>(buffer.data(), static_cast<std::ptrdiff_t>(read)));
            total += read;
        }
        auto const end = std::chrono::steady_clock::now();
        return {std::chrono::duration<double>(end - start).count(), total, short_reads, hash.final()};
    }

    read_result run_pread(std::string const &path, std::size_t const buffer_size)
    {
        unique_handle file = open_read_only(path);
        HANDLE const native_file = static_cast<HANDLE>(file.get());
        std::uint64_t const size = file_size(native_file);
        std::vector<char> buffer(buffer_size);
        libtorrent::hasher hash;
        std::uint64_t offset = 0;
        std::uint64_t short_reads = 0;

        auto const start = std::chrono::steady_clock::now();
        while (offset < size)
        {
            DWORD const request = static_cast<DWORD>(std::min<std::uint64_t>(buffer.size(), size - offset));
            OVERLAPPED overlapped {};
            overlapped.Offset = static_cast<DWORD>(offset & 0xffffffffu);
            overlapped.OffsetHigh = static_cast<DWORD>(offset >> 32);

            DWORD read = 0;
            if (!::ReadFile(native_file, buffer.data(), request, &read, &overlapped))
                throw std::runtime_error("pread-style ReadFile failed at offset " + std::to_string(offset)
                    + ": " + std::to_string(::GetLastError()));
            if (read == 0)
                break;
            if (read != request)
                ++short_reads;
            hash.update(libtorrent::span<char const>(buffer.data(), static_cast<std::ptrdiff_t>(read)));
            offset += read;
        }
        auto const end = std::chrono::steady_clock::now();
        return {std::chrono::duration<double>(end - start).count(), offset, short_reads, hash.final()};
    }

    void print_result(char const *const mode, int const run, options const &opts, read_result const &r)
    {
        double const mib = static_cast<double>(r.bytes) / (1024.0 * 1024.0);
        double const rate = (r.seconds > 0.0) ? mib / r.seconds : 0.0;
        std::cout << std::fixed << std::setprecision(2)
            << mode << " run=" << run << '/' << opts.runs << ": "
            << r.seconds << " s, " << rate << " MiB/s, bytes=" << r.bytes
            << ", short_reads=" << r.short_reads
            << ", sha1=" << digest_hex(r.digest) << '\n';
        std::cout << std::setprecision(6)
            << "WOFRESULT," << mode << ',' << opts.buffer_kib << ',' << run
            << ',' << r.seconds << ',' << r.bytes << ',' << rate << ',' << r.short_reads
            << ',' << digest_hex(r.digest) << '\n';
    }
}

int main(int const argc, char const *const argv[]) try
{
    options const opts = parse_options(argc, argv);
    std::size_t const buffer_size = static_cast<std::size_t>(opts.buffer_kib) * 1024;
    if (buffer_size > static_cast<std::size_t>((std::numeric_limits<DWORD>::max)()))
        throw std::invalid_argument("buffer size exceeds ReadFile DWORD limit");

    unique_handle file = open_read_only(opts.file);
    storage_sizes const sizes = query_storage_sizes(static_cast<HANDLE>(file.get()));
    wof_state const wof = query_wof(opts.file);

    std::cout << "file:        " << opts.file << '\n'
              << "logical:     " << sizes.logical << " bytes\n"
              << "allocation:  " << sizes.allocation << " bytes\n"
              << "buffer:      " << opts.buffer_kib << " KiB\n";

    if (FAILED(wof.status))
        std::cout << "WOF:         query failed, HRESULT=0x" << std::hex
                  << static_cast<unsigned long>(wof.status) << std::dec << '\n';
    else if (!wof.external)
        std::cout << "WOF:         no (regular physical file)\n";
    else if (wof.provider == WOF_PROVIDER_FILE)
        std::cout << "WOF:         yes, file provider, algorithm=" << algorithm_name(wof.algorithm) << '\n';
    else
        std::cout << "WOF:         yes, provider=" << wof.provider << '\n';

    bool mismatch = false;
    for (int run = 1; run <= opts.runs; ++run)
    {
        read_result const sync = run_sync(opts.file, buffer_size);
        read_result const pread = run_pread(opts.file, buffer_size);
        print_result("sync", run, opts, sync);
        print_result("pread", run, opts, pread);

        bool const same = (sync.bytes == pread.bytes)
            && (sync.digest == pread.digest)
            && (sync.bytes == sizes.logical);
        std::cout << "COMPARE,run=" << run << ",same=" << (same ? "yes" : "NO") << '\n';
        mismatch = mismatch || !same;
    }

    return mismatch ? 2 : 0;
}
catch (std::exception const &e)
{
    std::cerr << "wof_checking_probe: " << e.what() << '\n';
    return 1;
}

#else

#include <iostream>
int main()
{
    std::cerr << "wof_checking_probe is Windows-only\n";
    return 1;
}

#endif
