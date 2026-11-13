# C++ and OpenGL: RAII, const-correctness, and a state machine you can reason about

> For native code that has to run for hours: no leaks, no undefined behaviour, no silent GL errors.

## Instructions

You are writing or reviewing C++17/20 and OpenGL 3.3+ (desktop, no framework assumed).

C++
- RAII for every resource: a destructor, a `std::unique_ptr` with a custom deleter, or a scope guard. No naked `new`/`delete`, no `malloc` in C++ code.
- Rule of zero: if you need a destructor, you probably need the copy/move operations deleted or defined.
- `const` correctness: `const` methods that do not mutate, `const&` parameters, `constexpr` for compile-time constants.
- `std::string_view` / `std::span` for read-only views; never store them past the owner's lifetime.
- `at()` where an out-of-range index is a programming error you want loud; `[]` only after a size check.
- Exceptions for construction failures, error codes for hot paths; never both in the same API.
- Avoid undefined behaviour: initialize every member, no strict-aliasing tricks, no signed overflow, no pointer arithmetic past the end.

OpenGL
- The context is a state machine: every function assumes the right state. Wrap it — a small `Renderer` owns VAO/VBO/shader lifetimes.
- Create, bind, configure, unbind in that order; check `glGetError()` in debug builds only, and clear it before the call.
- Shaders: compile, check `GL_COMPILE_STATUS`, print the info log with the source line, and abort the program visibly on failure.
- VAOs remember attribute state; a missing `glEnableVertexAttribArray` renders nothing with no error. Bind the VAO *after* the buffer for setup.
- `glBufferData` with `GL_DYNAMIC_DRAW` for per-frame data; reallocate rather than patch when the layout changes.
- Thread affinity: the context lives on one thread. No GL call from a worker.
- Matrices: column-major, explicit projection/view/model, and the depth test with `glEnable(GL_DEPTH_TEST)` plus `glClear(GL_DEPTH_BUFFER_BIT)`.

Build and tests
- CMake with `target_compile_options(-Wall -Wextra -Wpedantic)`; sanitizers (`-fsanitize=address,undefined`) in debug CI.
- Unit-test the pure parts (math, mesh generation) headlessly; the render loop is verified by a screenshot harness, not by assertions on GL.
- Explicit destructor order on shutdown: GL objects first, context after.

Review checklist
- [ ] Every GL object owned by an RAII type
- [ ] No naked new/delete; rule of zero respected
- [ ] Shader compile failures reported with the log
- [ ] `glGetError` drained in debug; no silent failures
- [ ] Context used on one thread only
- [ ] ASan/UBSan clean in CI
