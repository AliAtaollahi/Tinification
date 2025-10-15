def abstractDotByLine(dot: String): String = {
  import scala.util.matching.Regex

  // Expect labels like: <KIND, 123<BR/>code...>
  val nodeRx = "\"(\\d+)\"\\s*\\[label\\s*=\\s*<([^,>]+),\\s*(\\d+)<BR/>(.*?)>\\s*\\]".r
  val edgeRx = "\"(\\d+)\"\\s*->\\s*\"(\\d+)\"".r

  case class Node(id: String, kind: String, line: String, code: String, methodName: Option[String] = None)

  val nodes: List[Node] =
    nodeRx.findAllMatchIn(dot).map { m =>
      val kind = m.group(2)
      val code = m.group(4)
      println(code)
      val methName =
        if (kind.startsWith("METHOD") || kind.toLowerCase.contains("init")) Some(code) else None
      Node(m.group(1), kind, m.group(3), code, methName)
    }.toList

  if (nodes.isEmpty)
    return """digraph "cfg_abstract" { node [shape="rect"]; }"""

  // Helper maps
  val nodeById: Map[String, Node] = nodes.map(n => n.id -> n).toMap

  // For each line, pick the node with longest 'code'
  val bestByLine: Map[String, Node] =
    nodes.groupBy(_.line).map { case (line, xs) =>
      line -> xs.maxBy(_.code.length)
    }

  // METHOD names (if you later want to enrich from Joern, do it here)
  val methodNames: Map[String, String] =
    nodes.collect {
      case n if n.kind.startsWith("METHOD") || n.kind.equalsIgnoreCase("init") => n.id -> n.code
    }.toMap

  // Build node labels for only the kept nodes (the "largest" per line)
  val keptNodes = bestByLine.values.toList
  val nodeLines = keptNodes
    .sortBy(n => n.line.toIntOption.getOrElse(Int.MaxValue))
    .map { n =>
      val label =
        n.kind match {
          case k if k.startsWith("METHOD") && methodNames.contains(n.id) => methodNames(n.id)
          case k if k.equalsIgnoreCase("init") && n.methodName.isDefined => n.methodName.get
          // If you truly want METHOD_RETURN to show the method name, you need a mapping from return->method.
          case _ => s"${n.kind}, ${n.line}<BR/>${n.code}"
        }
      s""""${n.id}" [label = <${label}> ]"""
    }

def isRoot(id: String): Boolean =
  nodeById.get(id).exists(n => n.kind.startsWith("METHOD") || n.kind.equalsIgnoreCase("init"))

val edgeLines =
  edgeRx.findAllMatchIn(dot).flatMap { m =>
    val srcId = m.group(1)  // Source node (caller)
    val dstId = m.group(2)  // Destination node (callee)

    // Get the method name for the destination node (called method)
    val dstMethodName = nodeById.get(dstId).flatMap(_.methodName)

    // If the destination is a method call, find the corresponding method definition
    val adjSrc = nodeById.get(srcId).flatMap(n => bestByLine.get(n.line)).map(_.id).getOrElse(srcId)
    val adjDst = nodeById.get(dstId).flatMap(n => bestByLine.get(n.line)).map(_.id).getOrElse(dstId)

    // Create edges if we have a valid method call and definition pair
    val callToDefEdge = dstMethodName.flatMap { methodName =>
      nodeById.collectFirst {
        case (id, n) if n.kind.startsWith("METHOD") && n.methodName.contains(methodName) => id
      }.map { methodDefId =>
        // Add an edge from the call to the method definition
        s""""$adjSrc" -> "$methodDefId" [label="call to method"]"""
      }
    }

    // Regular control flow edge (non-method call edges)
    val ctrlEdge = if (adjSrc != adjDst && !isRoot(adjDst)) {
      Some(s""""$adjSrc" -> "$adjDst" [label="ctrl dep"]""")
    } else None

    // Combine the edges
    List(callToDefEdge, ctrlEdge).flatten
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
