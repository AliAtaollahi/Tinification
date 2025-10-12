def abstractDotByLine(dot: String): String = {
  import scala.util.matching.Regex
  val nodeRx = "\"(\\d+)\"\\s*\\[label\\s*=\\s*<([^,>]+),\\s*(\\d+)<BR/>(.*?)>\\s*\\]".r
  val edgeRx = "\"(\\d+)\"\\s*->\\s*\"(\\d+)\"".r

  case class Node(id: String, kind: String, line: String, code: String, methodName: Option[String] = None)
  
  // First, try extracting all nodes
  val nodes = nodeRx.findAllMatchIn(dot).map { m =>
    // Try to identify method-related nodes and fetch the method name dynamically
    val methodName = if (m.group(2).startsWith("METHOD") || m.group(2).contains("init")) {
      Some(m.group(4))  // Fetch the method name from the code
    } else {
      None
    }
    
    Node(m.group(1), m.group(2), m.group(3), m.group(4), methodName)
  }.toList

  if (nodes.isEmpty)
    return """digraph "cfg_abstract" { node [shape="rect"]; }"""

  // Create a map to store method names dynamically using Joern's `cpg.method`
  val methodNames = nodes.collect {
    case n if n.kind.startsWith("METHOD") || n.kind == "init" => 
      // You could use Joern's API here to fetch methods dynamically based on `n.code` (method name)
      (n.id, n.code)  // For now, this is the placeholder, but you would use Joern to fetch the real method
  }.toMap

  // Update the labels for METHOD nodes with their real method names
  val nodeLines = 
    nodes.distinct.sortBy(_.line.toInt).map { n =>
      val label = n.kind match {
        case "METHOD" if methodNames.contains(n.id) => methodNames(n.id)  // Use the actual method name
        case "METHOD_RETURN" if n.methodName.isDefined => n.methodName.get  // Use method name for return too
        case "init" if n.methodName.isDefined => n.methodName.get  // Use init name
        case _ => s"${n.kind}, ${n.line}<BR/>${n.code}"
      }
      s""""${n.id}" [label = <${label}> ]"""
    }

  val edgeLines =
    edgeRx.findAllMatchIn(dot).flatMap { m =>
      val src = m.group(1)
      val dst = m.group(2)
      if (src != dst) Some(s""""$src" -> "$dst"""") else None
    }.toSet.toList

  s"""digraph "cfg_abstract" {
node [shape="rect"];
${(nodeLines ++ edgeLines).mkString("\n")}
}"""
}





def prefixIds(dot: String, prefix: String): String = {
  val idRx = "\"(\\d+)\"".r
  idRx.replaceAllIn(dot, m => "\"" + prefix + "_" + m.group(1) + "\"")
}

def stripDotWrapper(dot: String): String =
  dot.linesIterator
    .filterNot(_.trim.startsWith("digraph"))
    .filterNot(_.trim.startsWith("node "))
    .filterNot(_.trim == "}")
    .mkString("\n")

def ownerTypeOf(fullName: String): String =
  fullName.takeWhile(_ != '.') // e.g., "HVAC$Controller"

// ===== Build one big DOT with *one cluster per class/type* =====
// Restrict to your file; drop the filter to include all files
val methods =
  cpg.method
    .where(_.isExternal(false))
    .where(_.file.name(".*HVAC\\.java"))
    .l

val methodsByOwner = methods.groupBy(m => ownerTypeOf(m.fullName))

val clustersByClass: List[String] = methodsByOwner.toList.sortBy(_._1).flatMap { case (owner, methods) =>
  val ownerSan = owner.replaceAll("[^A-Za-z0-9_]", "_")
  val bodies: List[String] = methods.flatMap { m =>
    val raw = m.dotCfg.l.headOption.getOrElse("")
    if (raw.isEmpty) None
    else {
      val abs  = abstractDotByLine(raw)
      val pfx  = (owner + "__" + m.fullName).replaceAll("[^A-Za-z0-9_]", "_")
      val body = stripDotWrapper(prefixIds(abs, pfx))
      Some(body)
    }
  }
  if (bodies.isEmpty) None
  else Some(
s"""subgraph "cluster_$ownerSan" {
  label="$owner";
  ${bodies.mkString("\n")}
}"""
  )
}

val cfg_abstract_by_class: String =
  s"""digraph "program_cfg_abstract_by_class" {
compound=true;
node [shape="rect"];
${clustersByClass.mkString("\n\n")}
}"""

// Print (or save to file)
println(cfg_abstract_by_class)

// Example save:
// import java.nio.file.{Files,Paths}; import java.nio.charset.StandardCharsets
// Files.write(Paths.get("program_cfg_abstract_by_class.dot"), cfg_abstract_by_class.getBytes(StandardCharsets.UTF_8))
// Render: dot -Tpng program_cfg_abstract_by_class.dot -o program_cfg_abstract_by_class.png

