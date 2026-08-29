# Rust Rewrite Plan - Performance Optimization

## 📋 Overview

This document outlines the plan to rewrite the Google Drive Telegram Bot from Python to Rust while maintaining 100% feature parity and API compatibility.

## 🎯 Objectives

1. **Performance**: 3-5x faster response times
2. **Memory Efficiency**: 70% reduction in memory usage
3. **Concurrency**: True parallelism without GIL limitations
4. **Reliability**: Zero-cost abstractions and compile-time safety
5. **Maintainability**: Strong type system and clear error handling

## 📦 Project Structure

```
.
├── Cargo.toml                    # Rust project manifest
├── src/
│   ├── main.rs                   # Application entry point
│   ├── bot.rs                    # Bot core logic
│   ├── config.rs                 # Configuration management
│   ├── error.rs                  # Error handling
│   │
│   ├── handlers/                 # Command handlers
│   │   ├── mod.rs
│   │   ├── auth.rs               # /auth, /revoke
│   │   ├── search.rs             # /searchdrive
│   │   ├── list.rs               # /list
│   │   ├── copy.rs               # /copy
│   │   ├── move.rs               # /move
│   │   └── welcome.rs            # /start, /help
│   │
│   ├── services/                 # Business logic
│   │   ├── mod.rs
│   │   ├── gdrive.rs             # Google Drive API
│   │   ├── auth.rs               # Authentication
│   │   ├── telegram.rs           # Telegram API wrapper
│   │   └── file_ops.rs           # File operations
│   │
│   ├── ui/                       # UI components (Apple-style)
│   │   ├── mod.rs
│   │   ├── formatter.rs          # Message formatting
│   │   ├── keyboard.rs           # Keyboard layouts
│   │   ├── progress.rs           # Progress bars
│   │   └── icons.rs              # Emoji icons
│   │
│   ├── db/                       # Database operations
│   │   ├── mod.rs
│   │   ├── models.rs             # Data models
│   │   └── queries.rs            # SQL queries
│   │
│   └── utils/                    # Utilities
│       ├── mod.rs
│       ├── url_parser.rs         # URL parsing
│       └── cache.rs              # Caching layer
│
├── tests/                        # Integration tests
│   ├── integration_tests.rs
│   └── fixtures/
│
└── Cargo.lock
```

## 🔄 Migration Strategy

### Phase 1: Foundation (Week 1-2)
- [ ] Set up Rust project structure
- [ ] Configure dependencies (teloxide, google-drive-api, tokio, sqlx)
- [ ] Implement configuration and environment loading
- [ ] Set up database connection pool
- [ ] Create error handling framework

### Phase 2: Core Features (Week 3-4)
- [ ] Implement Google Drive API client
- [ ] Implement Telegram bot core
- [ ] Basic command routing
- [ ] Authentication handlers
- [ ] User session management

### Phase 3: Advanced Features (Week 5-6)
- [ ] File search functionality
- [ ] File listing with pagination
- [ ] File copy/move operations
- [ ] Progress tracking
- [ ] Error recovery

### Phase 4: UI & Polish (Week 7-8)
- [ ] Apple-style UI components
- [ ] Message formatting
- [ ] Inline keyboards
- [ ] Progress visualization
- [ ] Comprehensive testing

## 📊 Dependency Mapping

### Python Dependencies → Rust Crates

| Python Package | Rust Crate | Purpose |
|---|---|---|
| pyrogram | **teloxide** | Telegram Bot API |
| google-api-python-client | **google-drive3** | Google Drive API |
| SQLAlchemy | **sqlx** | Database ORM |
| aiohttp | **reqwest** | HTTP client |
| asyncio | **tokio** | Async runtime |
| cachetools | **lru** | Caching |
| yt-dlp | Custom wrapper | Video downloading |

## 🚀 Performance Targets

### Expected Improvements

| Metric | Python (Current) | Rust (Target) | Improvement |
|---|---|---|---|
| Command Response Time | ~500ms | ~150ms | **3x faster** |
| Memory Usage | ~200MB | ~60MB | **70% reduction** |
| Concurrent Users | 100 | 10,000+ | **100x more** |
| Database Queries | 50ms (avg) | 5ms (avg) | **10x faster** |
| Startup Time | ~5s | ~1s | **5x faster** |

## 🔐 API Compatibility

### Maintained API Contracts

1. **Command Interface** - All commands work identically
2. **Message Format** - Exact same formatting and styling
3. **Button Layouts** - Same keyboard structures
4. **Error Messages** - Consistent error handling
5. **Database Schema** - No changes to PostgreSQL tables

### Internal Changes (Transparent to Users)

1. **Type System** - Strong typing ensures correctness
2. **Concurrency** - True parallelism via tokio
3. **Memory Management** - Rust's ownership system
4. **Error Handling** - Result types instead of try/catch

## 📝 Development Checklist

### Setup
- [ ] Initialize Cargo project
- [ ] Configure Cargo.toml with all dependencies
- [ ] Set up .env.example for Rust
- [ ] Create Docker build configuration

### Core Handlers
- [ ] `/start` command
- [ ] `/help` command
- [ ] `/auth` command
- [ ] `/revoke` command
- [ ] `/searchdrive` command
- [ ] `/list` command
- [ ] `/copy` command
- [ ] `/move` command
- [ ] `/delete` command
- [ ] `/clone` command

### Services
- [ ] Google Drive authentication
- [ ] File search service
- [ ] File listing service
- [ ] File copy service
- [ ] File move service
- [ ] Delete/trash service
- [ ] Progress tracking service

### UI Components
- [ ] Message formatter
- [ ] Keyboard builder
- [ ] Progress bar renderer
- [ ] Icon definitions
- [ ] Error message templates

### Database
- [ ] User model
- [ ] Auth token storage
- [ ] Task management
- [ ] Session tracking

### Testing
- [ ] Unit tests for services
- [ ] Integration tests with mock APIs
- [ ] Command handler tests
- [ ] Database tests

## 🛠️ Key Libraries

```toml
[dependencies]
# Telegram Bot Framework
teloxide = "0.27"

# Google Drive API
google-drive3 = "4.1"
google-authz = "4.1"

# Async Runtime
tokio = { version = "1.35", features = ["full"] }

# HTTP Client
reqwest = { version = "0.11", features = ["json"] }

# Database
sqlx = { version = "0.7", features = ["postgres", "runtime-tokio"] }

# Serialization
serde = { version = "1.0", features = ["derive"] }
serde_json = "1.0"

# Environment Configuration
dotenv = "0.15"
config = "0.13"

# Caching
lru = "0.12"
cached = "0.46"

# Logging
tracing = "0.1"
tracing-subscriber = "0.3"

# Error Handling
anyhow = "1.0"
thiserror = "1.0"

# Video Downloading
yt-dlp = "0.2"  # Or use subprocess/shellexpand
```

## 🔄 Testing Strategy

1. **Unit Tests** - Pure function testing
2. **Integration Tests** - API endpoint testing with mocks
3. **Database Tests** - SQLx compile-time checked queries
4. **E2E Tests** - Full command flow with test bot

## 📊 Migration Validation

For each feature, we will:
1. Keep Python version running
2. Run Rust version in parallel
3. Compare outputs side-by-side
4. Validate API responses match exactly
5. Performance benchmark comparison

## 🚀 Deployment Strategy

1. **Blue-Green Deployment** - Run both versions simultaneously
2. **Canary Release** - Route 10% of traffic to Rust version
3. **Monitor Metrics** - Track response times and errors
4. **Rollback Plan** - Quick fallback to Python if needed
5. **Full Cutover** - Once stable, deprecate Python version

## ✅ Success Criteria

- [x] Zero breaking changes to public API
- [ ] All commands working with identical output
- [ ] Performance targets met (3x faster response)
- [ ] Memory usage reduced by 70%
- [ ] 100% test coverage for critical paths
- [ ] Comprehensive error handling
- [ ] Production-ready logging and monitoring
- [ ] Complete documentation and examples

## 📚 References

- [Teloxide Documentation](https://docs.rs/teloxide/)
- [Google Drive API Rust](https://docs.rs/google-drive3/)
- [Tokio Documentation](https://tokio.rs/)
- [SQLx Documentation](https://github.com/launchbadge/sqlx)
- [Rust API Guidelines](https://rust-api-guidelines.data-sled.rs/)

---

**Started**: 2026-08-29  
**Status**: Planning Phase  
**Next Review**: When Phase 1 is complete
