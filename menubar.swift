import AppKit

final class EffortMenu: NSObject, NSApplicationDelegate {
    private var item: NSStatusItem!
    private var worker: Process?
    private var pipe: Pipe?
    private var buffer = Data()
    private var snapshot: [String: Any] = [:]
    private var lastUpdate = Date.distantPast
    private var timer: Timer?
    private var chosenThread = UserDefaults.standard.string(forKey: "chosenThread")

    func applicationDidFinishLaunching(_ notification: Notification) {
        if NSRunningApplication.runningApplications(withBundleIdentifier: "io.github.mixx993.codex-auto-effort").count > 1 {
            NSApp.terminate(nil)
            return
        }
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.button?.font = NSFont.monospacedSystemFont(ofSize: 12, weight: .medium)
        refresh()
        startWorker()
        timer = Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { [weak self] _ in
            self?.refresh()
        }
    }

    private func startWorker() {
        guard let resources = Bundle.main.resourceURL,
              let data = try? Data(contentsOf: resources.appendingPathComponent("runtime.json")),
              let runtime = try? JSONSerialization.jsonObject(with: data) as? [String: String],
              let python = runtime["python"] else { return }
        let process = Process()
        let output = Pipe()
        process.executableURL = URL(fileURLWithPath: python)
        process.arguments = [resources.appendingPathComponent("monitor.py").path, "--watch"]
        process.standardOutput = output
        process.standardError = FileHandle.nullDevice
        output.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let incoming = handle.availableData
            if incoming.isEmpty {
                handle.readabilityHandler = nil
                return
            }
            DispatchQueue.main.async {
                guard let self = self else { return }
                self.buffer.append(incoming)
                while let newline = self.buffer.firstIndex(of: 10) {
                    let line = self.buffer.prefix(upTo: newline)
                    self.buffer.removeSubrange(...newline)
                    if let value = try? JSONSerialization.jsonObject(with: line) as? [String: Any] {
                        self.snapshot = value
                        self.lastUpdate = Date()
                        self.refresh()
                    }
                }
            }
        }
        do {
            try process.run()
            worker = process
            pipe = output
        } catch {
            output.fileHandleForReading.readabilityHandler = nil
        }
    }

    private func label(_ title: String, _ menu: NSMenu) {
        let entry = NSMenuItem(title: title, action: nil, keyEquivalent: "")
        entry.isEnabled = false
        menu.addItem(entry)
    }

    @discardableResult
    private func action(_ title: String, selector: Selector, menu: NSMenu) -> NSMenuItem {
        let entry = NSMenuItem(title: title, action: selector, keyEquivalent: "")
        entry.target = self
        menu.addItem(entry)
        return entry
    }

    private func timeLabel(_ value: Any?) -> String {
        guard let text = value as? String else { return "未知" }
        let parser = ISO8601DateFormatter()
        parser.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        guard let date = parser.date(from: text) else { return text }
        let formatter = DateFormatter()
        formatter.dateFormat = "MM-dd HH:mm:ss"
        return formatter.string(from: date)
    }

    private func refresh() {
        let tasks = snapshot["tasks"] as? [[String: Any]] ?? []
        let selected = chosenThread == nil ? tasks.first : tasks.first { $0["thread"] as? String == chosenThread }
        let stale = Date().timeIntervalSince(lastUpdate) > 5
        let disabled = snapshot["enabled"] as? Bool == false
        let menu = NSMenu()
        label("Codex Auto Effort", menu)
        label(chosenThread == nil ? "跟随最近自动请求 · 非当前窗口识别" : "固定查看所选任务", menu)
        var title = "Effort · —"
        var tip = "等待本地监测记录"
        if stale {
            title = "Effort · ?"
            tip = "监测尚未连接或已停止；不代表模型档位"
            label(tip, menu)
        } else if disabled {
            title = "Effort · OFF"
            tip = "自动选档已关闭，以下为历史记录"
            label(tip, menu)
        }
        if let task = selected {
            let actual = task["actual"] as? String
            let requested = task["requested"] as? String ?? "未知"
            let phase = task["phase"] as? String ?? "selected"
            let name = (task["title"] as? String ?? "任务").replacingOccurrences(of: "\n", with: " ")
            let status: String
            if actual != nil {
                status = "服务端记录已确认"
            } else if phase == "rejected" {
                status = "请求被拒绝 · 不能认定已采用"
            } else if phase == "accepted" {
                status = "请求已接受 · 实际档位待确认"
            } else {
                status = "已选择 · 等待服务端"
            }
            if !stale && !disabled {
                title = "Effort · " + (actual ?? requested).uppercased() + (actual != nil ? " ✓" : phase == "rejected" ? " !" : " …")
                tip = "\(name)\n\(status)\n最近记录：\(timeLabel(task["actual_time"] ?? task["time"]))"
            }
            menu.addItem(.separator())
            label(String(name.prefix(55)), menu)
            label("实际记录：\(actual ?? "待确认")", menu)
            label("状态：\(status)", menu)
            label("来源：\(task["source"] as? String ?? "本地请求日志")", menu)
            label("记录时间：\(timeLabel(task["actual_time"]))", menu)
            label(task["association"] as? String ?? "不会沿用上一轮的档位作为确认", menu)
            menu.addItem(.separator())
            label("最近自动选择：\(requested) · \(timeLabel(task["time"]))", menu)
            label("请求原档位：\(task["original"] as? String ?? "旧日志未记录")", menu)
            label("模型：\(task["model"] as? String ?? "待确认")", menu)
            label("原因：\(task["reason"] as? String ?? "未知")", menu)
        } else if !stale && !disabled {
            label(chosenThread == nil ? "尚无自动路由记录，发送一轮新消息后查看" : "所选任务不在最近 12 个任务中", menu)
        }
        item.button?.title = title
        item.button?.toolTip = tip
        menu.addItem(.separator())
        let automatic = action("跟随最近自动请求", selector: #selector(selectTask(_:)), menu: menu)
        automatic.state = chosenThread == nil ? .on : .off
        for task in tasks {
            let entry = action(String((task["title"] as? String ?? "任务").prefix(45)), selector: #selector(selectTask(_:)), menu: menu)
            entry.representedObject = task["thread"]
            entry.state = task["thread"] as? String == chosenThread ? .on : .off
        }
        menu.addItem(.separator())
        action("重新连接监测", selector: #selector(reconnect), menu: menu)
        action("打开项目说明", selector: #selector(openProject), menu: menu)
        action("退出状态栏（自动选档继续工作）", selector: #selector(quit), menu: menu)
        item.menu = menu
    }

    @objc private func selectTask(_ sender: NSMenuItem) {
        chosenThread = sender.representedObject as? String
        UserDefaults.standard.set(chosenThread, forKey: "chosenThread")
        refresh()
    }

    private func stopWorker() {
        pipe?.fileHandleForReading.readabilityHandler = nil
        if worker?.isRunning == true { worker?.terminate() }
        worker = nil
        pipe = nil
        buffer.removeAll()
    }

    @objc private func reconnect() {
        stopWorker()
        lastUpdate = .distantPast
        startWorker()
        refresh()
    }

    @objc private func openProject() {
        NSWorkspace.shared.open(URL(string: "https://github.com/mixx993/codex-auto-effort")!)
    }

    @objc private func quit() { NSApp.terminate(nil) }

    func applicationWillTerminate(_ notification: Notification) {
        timer?.invalidate()
        stopWorker()
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let delegate = EffortMenu()
app.delegate = delegate
app.run()
