import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.*;
import ghidra.program.model.listing.*;
import java.io.*;

public class DumpAll extends GhidraScript {
    @Override
    public void run() throws Exception {
        DecompInterface d = new DecompInterface();
        d.openProgram(currentProgram);
        PrintWriter pw = new PrintWriter(new FileWriter("/opt/re/out/decomp.c"));
        FunctionIterator it = currentProgram.getFunctionManager().getFunctions(true);
        int n = 0;
        while (it.hasNext()) {
            Function f = it.next();
            DecompileResults r = d.decompileFunction(f, 60, monitor);
            pw.println("// ==== " + f.getName() + " @ " + f.getEntryPoint());
            if (r != null && r.decompileCompleted()) {
                pw.println(r.getDecompiledFunction().getC());
            } else {
                pw.println("// decompile failed");
            }
            n++;
        }
        pw.close();
        println("functions: " + n);
    }
}
