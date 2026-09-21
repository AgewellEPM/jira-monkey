import Foundation

// This adapter is compiled with the unmodified Kist Captain sources. Inputs are
// produced by Monkey's host evidence verifier, never by the conversation model.
struct Input: Decodable {
    let buildID: String
    let claimedDone: Bool
    let exitCode: Int32
    let changedLines: Int
    let maxFileLines: Int
    let operatorReviewed: Bool
    let incomplete: Bool
    let confirmedExactOutcome: Bool
    let evidenceScope: String?
}

@main struct MonkeyCaptain {
    static func main() throws {
        let data = FileHandle.standardInput.readDataToEndOfFile()
        guard data.count < 8192 else { exit(2) }
        let input = try JSONDecoder().decode(Input.self, from: data)
        let build = BuildFacts(buildID: input.buildID, claimedDone: input.claimedDone,
            tests: TestRunEvidence(exitCode: input.exitCode),
            diff: DiffEvidence(changedLines: input.changedLines, maxFileLines: input.maxFileLines),
            codex: CodexEvidence(reviewed: input.operatorReviewed), stepCapHit: false,
            runIncomplete: input.incomplete)
        let world = WorldFacts(confirmedHelpForBuild: input.confirmedExactOutcome ? 1 : 0,
                               signalBroken: false, hasSignal: input.confirmedExactOutcome)
        let judgment = SymbolicCaptain().judge(CaptainFacts(build: build, world: world))
        let result: [String: Any] = ["verdict": judgment.verdict.rawValue,
            "failed_rules": judgment.failedBlockers, "warnings": judgment.warnings,
            "world_signal_missing": judgment.worldSignalMissing,
            "scope": input.evidenceScope ?? "exact operator-reviewed local project result; no deployment claim"]
        FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject: result, options: [.sortedKeys]))
    }
}
