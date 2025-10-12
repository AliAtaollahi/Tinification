// ===== Helpers: (same abstraction as before) one node per *source line* =====
def abstractDotByLine(dot: String): String = {
  import scala.util.matching.Regex
  val nodeRx = "\"(\\d+)\"\\s*\\[label\\s*=\\s*<([^,>]+),\\s*(\\d+)<BR/>(.*?)>\\s*\\]".r
  val edgeRx = "\"(\\d+)\"\\s*->\\s*\"(\\d+)\"".r

  case class Node(id: String, kind: String, line: String, code: String)
  val nodes = nodeRx.findAllMatchIn(dot).map(m => Node(m.group(1), m.group(2), m.group(3), m.group(4))).toList

  if (nodes.isEmpty)
    return """digraph "cfg_abstract" { node [shape="rect"]; }"""
  
    // representative per line (largest label)
  val repByLine = nodes.groupBy(_.line).view.mapValues(ns => ns.maxBy(n => n.kind.length + n.code.length)).toMap
  val id2rep = nodes.map(n => n.id -> repByLine(n.line).id).toMap
  val repIds = repByLine.values.map(_.id).toSet

  val nodeLines =
    repByLine.values.toList.distinct.sortBy(_.line.toInt).map { n =>
      s""""${n.id}" [label = <${n.kind}, ${n.line}<BR/>${n.code}> ]"""
    }

  val edgeLines =
    edgeRx.findAllMatchIn(dot).flatMap { m =>
      val src = id2rep.getOrElse(m.group(1), m.group(1))
      val dst = id2rep.getOrElse(m.group(2), m.group(2))
      if (src != dst && repIds(src) && repIds(dst)) Some(s""""$src" -> "$dst"""") else None
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
