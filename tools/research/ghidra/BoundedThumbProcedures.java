// @category A6400.Research
import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import ghidra.app.cmd.disassemble.DisassembleCommand;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.address.AddressSet;
import ghidra.program.model.lang.Register;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.symbol.SourceType;
import java.io.Reader;
import java.io.Writer;
import java.math.BigInteger;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayDeque;
import java.util.HashSet;

public class BoundedThumbProcedures extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length != 3) {
            throw new IllegalArgumentException("Expected query JSON, output JSON, and source SHA-256");
        }
        if (Files.exists(Path.of(args[1]))) {
            throw new IllegalArgumentException("Refusing to overwrite an existing analysis receipt");
        }
        JsonObject report = new JsonObject();
        report.addProperty("status", "failed");
        report.addProperty("source_sha256", currentProgram.getExecutableSHA256());
        report.addProperty("analysis_policy",
            "Explicit observed Thumb entries; bounded disassembly; no global auto-analysis");
        report.addProperty("limitation",
            "Reconstructed prototypes and external flows remain unverified; this is not target execution.");
        JsonArray results = new JsonArray();
        report.add("procedures", results);
        Gson gson = new GsonBuilder().setPrettyPrinting().create();
        DecompInterface decompiler = new DecompInterface();
        try {
            if (!args[2].equalsIgnoreCase(currentProgram.getExecutableSHA256())) {
                throw new IllegalArgumentException("Imported source SHA-256 differs from the declared input");
            }
            if (!currentProgram.getLanguageID().toString().equals("ARM:LE:32:v7")) {
                throw new IllegalArgumentException("Expected the explicit ARM:LE:32:v7 raw profile");
            }
            JsonArray queries;
            try (Reader reader = Files.newBufferedReader(Path.of(args[0]), StandardCharsets.UTF_8)) {
                queries = JsonParser.parseReader(reader).getAsJsonArray();
            }
            if (queries.size() < 1 || queries.size() > 16) {
                throw new IllegalArgumentException("Expected one to sixteen bounded queries");
            }
            Register thumb = currentProgram.getRegister("TMode");
            if (thumb == null) {
                throw new IllegalArgumentException("Selected language has no Thumb context register");
            }
            int totalBytes = 0;
            HashSet<Address> declaredEntries = new HashSet<>();
            for (var item : queries) {
                JsonObject query = item.getAsJsonObject();
                Address entry = toAddr(query.get("entry").getAsString());
                int length = query.get("bytes").getAsInt();
                totalBytes += length;
                if ((entry.getOffset() & 1) != 0 || length < 2 || length > 4096 ||
                        (length & 1) != 0 || totalBytes > 32768) {
                    throw new IllegalArgumentException("Query exceeds the Thumb address/size bound");
                }
                Address end = entry.add(length - 1);
                if (!currentProgram.getMemory().contains(entry) ||
                        !currentProgram.getMemory().contains(end)) {
                    throw new IllegalArgumentException("Query is not backed by the imported image");
                }
                currentProgram.getProgramContext().setValue(thumb, entry, end, BigInteger.ONE);
                declaredEntries.add(entry);
            }
            for (var item : queries) {
                JsonObject query = item.getAsJsonObject();
                Address entry = toAddr(query.get("entry").getAsString());
                Address end = entry.add(query.get("bytes").getAsInt() - 1);
                DisassembleCommand command = new DisassembleCommand(entry, new AddressSet(entry, end), true);
                if (!command.applyTo(currentProgram, monitor)) {
                    throw new IllegalStateException("Bounded disassembly failed: " + command.getStatusMsg());
                }
            }
            if (!decompiler.openProgram(currentProgram)) {
                throw new IllegalStateException("Decompiler could not open the imported program");
            }
            boolean complete = true;
            for (var item : queries) {
                JsonObject query = item.getAsJsonObject();
                Address entry = toAddr(query.get("entry").getAsString());
                Address end = entry.add(query.get("bytes").getAsInt() - 1);
                JsonObject result = new JsonObject();
                result.add("query", query.deepCopy());
                JsonArray outside = new JsonArray();
                AddressSet body = new AddressSet();
                ArrayDeque<Address> pending = new ArrayDeque<>();
                HashSet<Address> seen = new HashSet<>();
                pending.add(entry);
                while (!pending.isEmpty()) {
                    Address address = pending.removeFirst();
                    if (!seen.add(address)) {
                        continue;
                    }
                    if (address.compareTo(entry) < 0 || address.compareTo(end) > 0) {
                        outside.add(address.toString());
                        continue;
                    }
                    if (!address.equals(entry) && declaredEntries.contains(address)) {
                        outside.add("other-declared-entry:" + address);
                        continue;
                    }
                    Instruction instruction = getInstructionAt(address);
                    if (instruction == null) {
                        outside.add("undecoded:" + address);
                        continue;
                    }
                    if (instruction.getMaxAddress().compareTo(end) > 0) {
                        outside.add("instruction-crosses-query-bound:" + address);
                        continue;
                    }
                    body.add(instruction.getMinAddress(), instruction.getMaxAddress());
                    if (!instruction.getFlowType().isCall()) {
                        for (Address flow : instruction.getFlows()) {
                            pending.add(flow);
                        }
                    }
                    if (instruction.getFallThrough() != null) {
                        pending.add(instruction.getFallThrough());
                    }
                }
                result.add("flows_outside_query", outside);
                if (body.isEmpty()) {
                    result.addProperty("status", "failed");
                    result.addProperty("error", "No decoded instructions at the declared Thumb entry");
                    complete = false;
                } else {
                    Function function = currentProgram.getFunctionManager().createFunction(
                        query.get("label").getAsString(), entry, body, SourceType.USER_DEFINED);
                    JsonArray instructions = new JsonArray();
                    var iterator = currentProgram.getListing().getInstructions(body, true);
                    while (iterator.hasNext()) {
                        Instruction instruction = iterator.next();
                        instructions.add(instruction.getAddress() + ": " + instruction);
                    }
                    result.add("instructions", instructions);
                    result.addProperty("body_bytes", body.getNumAddresses());
                    decompiler.flushCache();
                    DecompileResults decoded = decompiler.decompileFunction(function, 15, monitor);
                    if (!decoded.decompileCompleted() || decoded.getDecompiledFunction() == null) {
                        result.addProperty("status", "failed");
                        result.addProperty("error", decoded.getErrorMessage());
                        complete = false;
                    } else {
                        result.addProperty("status", "completed");
                        result.addProperty("pseudocode", decoded.getDecompiledFunction().getC());
                    }
                }
                results.add(result);
                println("BOUNDED_THUMB " + query.get("entry") + " " + result.get("status"));
            }
            report.addProperty("status", complete ? "completed" : "boundary");
        } finally {
            decompiler.dispose();
            try (Writer writer = Files.newBufferedWriter(Path.of(args[1]), StandardCharsets.UTF_8)) {
                gson.toJson(report, writer);
            }
        }
    }
}
