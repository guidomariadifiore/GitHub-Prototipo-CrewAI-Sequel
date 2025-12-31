import java.io.BufferedReader;
import java.io.FileReader;
import java.io.IOException;
import java.util.List;
import java.util.stream.Collectors;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

public class EnergyEfficientRefactoredCode {

    private static final int CORE_COUNT = Runtime.getRuntime().availableProcessors();
    private static final ExecutorService executor = Executors.newFixedThreadPool(CORE_COUNT);

    public List<String> processData(List<String> data) {
        return data.parallelStream()
                   .map(String::toLowerCase)
                   .collect(Collectors.toList());
    }

    public List<String> readAndProcessFile(String filePath) throws IOException, InterruptedException {
        List<String> lines;
        try (BufferedReader reader = new BufferedReader(new FileReader(filePath))) {
            lines = reader.lines()
                          .parallel()
                          .map(this::transform)
                          .collect(Collectors.toList());
        }
        return lines;
    }

    private String transform(String line) {
        StringBuilder sb = new StringBuilder();
        for (String part : line.split("\\s+")) { 
            sb.append(part.toUpperCase());
            sb.append("_"); 
        }
        if (sb.length() > 0) {
            sb.setLength(sb.length() - 1);
        }
        return sb.toString();
    }

    public void shutdownExecutor() {
        executor.shutdown();
        try {
            if (!executor.awaitTermination(30, TimeUnit.SECONDS)) {
                executor.shutdownNow();
            }
        } catch (InterruptedException e) {
            executor.shutdownNow();
            Thread.currentThread().interrupt();
        }
    }

    private int sumNumbers(List<Integer> numbers) {
        return numbers.stream()
                      .mapToInt(Integer::intValue)
                      .sum();
    }

    private volatile Object resource;

    public Object getResource() {
        if (resource == null) {
            synchronized (this) {
                if (resource == null) {
                    resource = initializeResource();
                }
            }
        }
        return resource;
    }

    private Object initializeResource() {
        try {
            Thread.sleep(50);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
        return new Object();
    }
}